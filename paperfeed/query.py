"""Turn one written query into the two search languages PaperFeed speaks.

A keyword set can say what it wants as a boolean expression:

    (IDH1 OR "isocitrate dehydrogenase 1") AND (glioma OR glioblastoma)

That one line has to reach PubMed as IDH1[Title/Abstract] AND ... and Europe
PMC as (TITLE:"IDH1" OR ABSTRACT:"IDH1") AND ... - two syntaxes with different
capabilities. So the query is parsed into a small tree first, and each engine
gets its own translation of that tree.

Parsing it instead of passing the string through also buys the thing that
matters most here: a query can be checked BEFORE it is sent. Neither search
engine will tell you a query is wrong. Measured against the live APIs:

    (IDH1[tiab] AND glioma[tiab]    unbalanced bracket -> HTTP 200, 2088 hits
    IDH1[notafield]                 invented field     -> HTTP 200, 2254 hits
    IDH1[tiab] ANDD glioma[tiab]    typo               -> HTTP 200, 0 hits
    BOGUSFIELD:"glioma" (EuropePMC) invented field     -> HTTP 200, 0 hits

Every one of those is a successful request that returns the wrong papers.
A query you cannot validate is a query you cannot trust.
"""

import re
from dataclasses import dataclass, field as dataclass_field
from typing import List

# The scopes a term can be searched in. "ti_ab" is the default because it is
# what keeps the noise down; "all" opens it up to full text and indexing.
FIELDS = ("ti_ab", "title", "abstract", "author", "journal", "mesh", "all")

FIELD_HELP = {
    "ti_ab": "title and abstract",
    "title": "title only",
    "abstract": "abstract only",
    "author": "author name, written surname-then-initials",
    "journal": "journal or preprint server name",
    "mesh": "MeSH heading, PubMed's curated subject index",
    "all": "everywhere, including full text",
}

OPERATORS = ("AND", "OR", "NOT")


class QueryError(Exception):
    """A query that cannot be parsed, phrased for the person who wrote it."""


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------

@dataclass
class Term:
    """One thing to look for, in one place."""
    text: str
    field: str = "ti_ab"
    phrase: bool = False      # was it written in quotes


@dataclass
class Group:
    """Several things joined by AND or OR."""
    op: str                   # "and" or "or"
    children: List[object] = dataclass_field(default_factory=list)


@dataclass
class Not:
    """Everything except this."""
    child: object


# --------------------------------------------------------------------------
# Reading the query
# --------------------------------------------------------------------------

def _point_at(text, position, message):
    """An error that shows the reader exactly where the trouble is."""
    return "%s\n      %s\n      %s^" % (message, text, " " * max(position, 0))


def _tokenize(text):
    """Split the query into brackets, operators, field prefixes and terms."""
    tokens = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char.isspace():
            index += 1
            continue

        if char == "(":
            tokens.append(("lparen", "(", index))
            index += 1
            continue

        if char == ")":
            tokens.append(("rparen", ")", index))
            index += 1
            continue

        if char == '"':
            end = text.find('"', index + 1)
            if end == -1:
                raise QueryError(_point_at(
                    text, index, "This quote is never closed."))
            phrase = text[index + 1:end].strip()
            if not phrase:
                raise QueryError(_point_at(
                    text, index, "Empty quotes - there is nothing to search for."))
            tokens.append(("phrase", phrase, index))
            index = end + 1
            continue

        # A bare run, ending at whitespace, a bracket or a quote. Stopping at
        # the quote is what makes title:"binder design" work: the run is just
        # the prefix 'title:' and the phrase is tokenized on the next pass.
        start = index
        while index < length and not text[index].isspace() and text[index] not in '()"':
            index += 1
        word = text[start:index]

        if ":" in word:
            prefix, _, rest = word.partition(":")
            name = prefix.strip().lower()
            if name not in FIELDS:
                raise QueryError(_point_at(
                    text, start,
                    "%r is not a field PaperFeed knows. Use one of: %s."
                    % (prefix, ", ".join(FIELDS))))
            tokens.append(("field", name, start))
            if rest:
                tokens.append(("word", rest, start + len(prefix) + 1))
            continue

        if word in OPERATORS:
            tokens.append((word.lower(), word, start))
            continue

        if word.upper() in OPERATORS:
            raise QueryError(_point_at(
                text, start,
                "Write %s in capitals. Lower-case %r is treated as a word to "
                "search for, not as an operator." % (word.upper(), word)))

        tokens.append(("word", word, start))

    return tokens


_ATOM_STARTS = ("word", "phrase", "lparen", "field")


class _Parser:
    """Recursive descent over the tokens.

        expression := or
        or         := and (OR and)*
        and        := unary (AND unary)*
        unary      := NOT unary | atom
        atom       := field? ( "(" expression ")" | word | phrase )
    """

    def __init__(self, tokens, text):
        self.tokens = tokens
        self.text = text
        self.position = 0

    def peek(self):
        if self.position < len(self.tokens):
            return self.tokens[self.position]
        return (None, None, len(self.text))

    def take(self):
        token = self.peek()
        self.position += 1
        return token

    def parse(self, default_field):
        if not self.tokens:
            raise QueryError("The query is empty.")
        node = self.parse_or(default_field)
        kind, value, where = self.peek()
        if kind is not None:
            if kind == "rparen":
                raise QueryError(_point_at(
                    self.text, where, "A closing bracket with no opening one."))
            raise QueryError(_point_at(
                self.text, where, "Could not make sense of the rest of this."))
        return node

    def parse_or(self, default_field):
        children = [self.parse_and(default_field)]
        while self.peek()[0] == "or":
            self.take()
            children.append(self.parse_and(default_field))
        return children[0] if len(children) == 1 else Group("or", children)

    def parse_and(self, default_field):
        children = [self.parse_unary(default_field)]
        while True:
            kind, _, where = self.peek()
            if kind == "and":
                self.take()
                children.append(self.parse_unary(default_field))
                continue
            # Two terms side by side with nothing between them. Guessing AND
            # here would be a silent decision about what the query means, and
            # silent decisions are exactly what this module exists to stop.
            if kind in _ATOM_STARTS:
                raise QueryError(_point_at(
                    self.text, where,
                    "Two terms next to each other with nothing between them. "
                    "Put AND or OR here, or put the whole thing in quotes to "
                    "search for it as a phrase."))
            break
        return children[0] if len(children) == 1 else Group("and", children)

    def parse_unary(self, default_field):
        if self.peek()[0] == "not":
            self.take()
            kind, _, where = self.peek()
            if kind not in _ATOM_STARTS and kind != "not":
                raise QueryError(_point_at(
                    self.text, where, "NOT has nothing after it."))
            return Not(self.parse_unary(default_field))
        return self.parse_atom(default_field)

    def parse_atom(self, default_field):
        kind, value, where = self.peek()

        scope = default_field
        if kind == "field":
            self.take()
            scope = value
            kind, value, where = self.peek()
            if kind not in _ATOM_STARTS or kind == "field":
                raise QueryError(_point_at(
                    self.text, where,
                    "This field prefix has nothing after it. Write it like "
                    "title:IDH1 or title:\"binder design\"."))

        if kind == "lparen":
            self.take()
            node = self.parse_or(scope)
            if self.peek()[0] != "rparen":
                raise QueryError(_point_at(
                    self.text, self.peek()[2],
                    "This bracket is never closed."))
            self.take()
            return node

        if kind in ("word", "phrase"):
            self.take()
            return Term(value, scope, phrase=(kind == "phrase"))

        if kind is None:
            raise QueryError(_point_at(
                self.text, where, "The query stops here, mid-way through."))
        raise QueryError(_point_at(
            self.text, where, "Expected something to search for here."))


def parse(text, default_field="ti_ab"):
    """Read a query string. Raises QueryError with a readable explanation."""
    if default_field not in FIELDS:
        raise QueryError("%r is not a field. Use one of: %s."
                         % (default_field, ", ".join(FIELDS)))
    if not (text or "").strip():
        raise QueryError("The query is empty.")
    if "[" in text or "]" in text:
        raise QueryError(
            "Square brackets are PubMed's own syntax and mean nothing to "
            "Europe PMC, so PaperFeed does not accept them in a query. Use a "
            "field prefix instead: mesh:\"Glioma\" rather than "
            "\"Glioma\"[MeSH].")
    return _Parser(_tokenize(text), text).parse(default_field)


# --------------------------------------------------------------------------
# Walking the tree
# --------------------------------------------------------------------------

def leaves(node):
    """Every Term in the tree, exclusions included."""
    if isinstance(node, Term):
        return [node]
    if isinstance(node, Not):
        return leaves(node.child)
    if isinstance(node, Group):
        found = []
        for child in node.children:
            found.extend(leaves(child))
        return found
    return []


def positive_leaves(node):
    """Every Term the paper must MATCH - what is under a NOT is skipped.

    This is what the scorer wants: a paper earns nothing for a word you
    asked to exclude.
    """
    if isinstance(node, Term):
        return [node]
    if isinstance(node, Not):
        return []
    if isinstance(node, Group):
        found = []
        for child in node.children:
            found.extend(positive_leaves(child))
        return found
    return []


def concept_groups(node):
    """The top-level AND groups: the distinct concepts the query requires.

    In (IDH1 OR "isocitrate dehydrogenase 1") AND (glioma OR glioblastoma)
    there are two concepts, each written several ways. Scoring by concept
    rather than by term is what stops a review that says "glioma",
    "glioblastoma" and "astrocytoma" from being rewarded three times for
    saying one thing three ways.
    """
    if isinstance(node, Group) and node.op == "and":
        return [child for child in node.children if positive_leaves(child)]
    return [node] if positive_leaves(node) else []


# --------------------------------------------------------------------------
# Speaking PubMed
# --------------------------------------------------------------------------

# Measured, not assumed: PubMed has no abstract-only tag. IDH1[ab] is silently
# treated as All Fields (6650 hits) where IDH1[tiab] gives 6189.
PUBMED_TAGS = {
    "ti_ab": "[Title/Abstract]",
    "title": "[Title]",
    "abstract": "[Title/Abstract]",
    "author": "[Author]",
    "journal": "[Journal]",
    "mesh": "[MeSH Terms]",
    "all": "",
}


def _quoted(text):
    clean = text.replace('"', "").strip()
    if "*" in clean:
        return clean          # quoting would disable PubMed's truncation
    return '"%s"' % clean


def to_pubmed(node, notes=None):
    """Render the tree as a PubMed query string.

    `notes` collects the places where PubMed cannot do what was asked, so
    they can be shown to the reader instead of silently applied.
    """
    if notes is None:
        notes = []

    if isinstance(node, Term):
        if node.field == "abstract":
            notes.append(
                "abstract:%s - PubMed has no abstract-only field, so this "
                "searched the title as well." % node.text)
        return "%s%s" % (_quoted(node.text), PUBMED_TAGS[node.field])

    if isinstance(node, Not):
        return "NOT %s" % _wrap(to_pubmed(node.child, notes))

    if isinstance(node, Group):
        joiner = " AND " if node.op == "and" else " OR "
        return "(%s)" % joiner.join(
            to_pubmed(child, notes) for child in node.children)

    raise QueryError("Cannot translate %r." % (node,))


def _wrap(rendered):
    return rendered if rendered.startswith("(") else "(%s)" % rendered


# --------------------------------------------------------------------------
# Speaking Europe PMC
# --------------------------------------------------------------------------

EPMC_FIELDS = {
    "ti_ab": ("TITLE", "ABSTRACT"),
    "title": ("TITLE",),
    "abstract": ("ABSTRACT",),
    "author": ("AUTH",),
    "journal": ("JOURNAL",),
    "mesh": ("MESH",),
    "all": (),
}


def to_europepmc(node, notes=None):
    """Render the tree as a Europe PMC query string."""
    if notes is None:
        notes = []

    if isinstance(node, Term):
        tags = EPMC_FIELDS[node.field]
        text = _quoted(node.text)
        if not tags:
            return text
        if len(tags) == 1:
            return '%s:%s' % (tags[0], text)
        return "(%s)" % " OR ".join('%s:%s' % (tag, text) for tag in tags)

    if isinstance(node, Not):
        return "NOT %s" % _wrap(to_europepmc(node.child, notes))

    if isinstance(node, Group):
        joiner = " AND " if node.op == "and" else " OR "
        return "(%s)" % joiner.join(
            to_europepmc(child, notes) for child in node.children)

    raise QueryError("Cannot translate %r." % (node,))


# --------------------------------------------------------------------------
# Preprints have no MeSH
# --------------------------------------------------------------------------

ALWAYS_FALSE = "ALWAYS_FALSE"
ALWAYS_TRUE = "ALWAYS_TRUE"


def for_preprints(node, notes=None):
    """Rewrite the tree for a preprint search, or return None.

    MeSH headings are assigned by MEDLINE indexers after a paper is accepted.
    A preprint has never been indexed, so a mesh: term against a preprint is
    not missing data - it is false. Propagating that truth is more honest
    than deleting the term, which would quietly change what the query means:
    dropping mesh:X from (A AND mesh:X) would widen the search, and dropping
    it from (A OR mesh:X) would narrow it.

    Returns None when no preprint can possibly match.
    """
    if notes is None:
        notes = []
    simplified = _simplify(node, notes)
    if simplified is ALWAYS_FALSE:
        notes.append(
            "every branch of this query needs a MeSH heading, and preprints "
            "have none, so no preprint can match it")
        return None
    if simplified is ALWAYS_TRUE:
        notes.append(
            "after removing the MeSH parts this query would match every "
            "preprint, so the preprint search was skipped")
        return None
    return simplified


def _simplify(node, notes):
    if isinstance(node, Term):
        if node.field == "mesh":
            notes.append(
                "mesh:%s was left out of the preprint search - preprints are "
                "not MeSH-indexed" % node.text)
            return ALWAYS_FALSE
        return node

    if isinstance(node, Not):
        inner = _simplify(node.child, notes)
        if inner is ALWAYS_FALSE:
            return ALWAYS_TRUE
        if inner is ALWAYS_TRUE:
            return ALWAYS_FALSE
        return Not(inner)

    if isinstance(node, Group):
        parts = [_simplify(child, notes) for child in node.children]
        if node.op == "and":
            if any(part is ALWAYS_FALSE for part in parts):
                return ALWAYS_FALSE
            kept = [part for part in parts if part is not ALWAYS_TRUE]
            if not kept:
                return ALWAYS_TRUE
        else:
            if any(part is ALWAYS_TRUE for part in parts):
                return ALWAYS_TRUE
            kept = [part for part in parts if part is not ALWAYS_FALSE]
            if not kept:
                return ALWAYS_FALSE
        return kept[0] if len(kept) == 1 else Group(node.op, kept)

    return node


# --------------------------------------------------------------------------
# Reading a query back in plain words
# --------------------------------------------------------------------------

def describe(node, indent=0):
    """The tree as an indented outline, for `check --explain`."""
    pad = "  " * indent
    if isinstance(node, Term):
        where = FIELD_HELP.get(node.field, node.field)
        return "%s%s   (in %s)" % (pad, node.text, where)
    if isinstance(node, Not):
        return "%sNOT\n%s" % (pad, describe(node.child, indent + 1))
    if isinstance(node, Group):
        lines = ["%s%s of:" % (pad, "ALL" if node.op == "and" else "ANY")]
        lines.extend(describe(child, indent + 1) for child in node.children)
        return "\n".join(lines)
    return "%s?" % pad


def unparse(node):
    """Back to query text - used to write a migrated query into config."""
    if isinstance(node, Term):
        text = '"%s"' % node.text if (node.phrase or " " in node.text) else node.text
        return text if node.field == "ti_ab" else "%s:%s" % (node.field, text)
    if isinstance(node, Not):
        return "NOT %s" % unparse(node.child)
    if isinstance(node, Group):
        joiner = " AND " if node.op == "and" else " OR "
        return "(%s)" % joiner.join(unparse(child) for child in node.children)
    return ""


# --------------------------------------------------------------------------
# Bringing the old settings forward
# --------------------------------------------------------------------------

class NotMigratable(Exception):
    """A keyword set that cannot be converted without a human decision."""


def from_legacy(keyword_set):
    """Write a set's old terms / all_of / authors as one query string.

    Raises NotMigratable when a term carries raw PubMed syntax: that syntax
    means nothing to Europe PMC, so converting it would be a guess.
    """
    terms = list(keyword_set.get("terms") or [])
    all_of = list(keyword_set.get("all_of") or [])
    authors = list(keyword_set.get("authors") or [])

    for term in terms + all_of:
        if "[" in term:
            raise NotMigratable(
                "%r contains PubMed field syntax. Rewrite it by hand using a "
                "field prefix, e.g. mesh:\"Glioma\"." % term)

    if not (terms or all_of or authors):
        raise NotMigratable("this set has nothing to search for")

    def as_term(text):
        return Term(text, "ti_ab", phrase=" " in text)

    groups = []
    if terms:
        groups.append(Group("or", [as_term(term) for term in terms])
                      if len(terms) > 1 else as_term(terms[0]))
    for term in all_of:
        groups.append(as_term(term))
    if authors:
        author_terms = [Term(name, "author", phrase=True) for name in authors]
        groups.append(Group("or", author_terms)
                      if len(author_terms) > 1 else author_terms[0])

    return unparse(groups[0] if len(groups) == 1 else Group("and", groups))


CHEATSHEET = """\
Writing a PaperFeed query
-------------------------
  IDH1                       one word, looked for in title and abstract
  "isocitrate dehydrogenase" words in quotes are an exact phrase
  A OR B                     either one
  A AND B                    both
  NOT A                      leave these out
  ( ... )                    brackets group things together

Narrowing where to look, by putting a field in front:
  title:IDH1                 title only - the strongest signal of what a
                             paper is actually about
  abstract:"survival"        abstract only (PubMed cannot do this and will
                             search the title too - it says so when it does)
  author:"Baker D"           surname then initials, as PubMed writes it
  journal:"Neuro-Oncology"   journal or preprint server
  mesh:"Glioma"              PubMed's curated subject index. Assigned only
                             after a paper is indexed, so the newest papers
                             and every preprint have none - use it as an OR
                             alternative, never as the only requirement
  all:CRISPR                 everywhere, including full text

A prefix carries into brackets, so this scopes all three to the title:
  title:(IDH1 OR IDH2 OR "isocitrate dehydrogenase")

Rules worth remembering:
  * AND, OR and NOT must be in capitals.
  * Two terms cannot sit side by side - put AND or OR between them.
  * Group your synonyms, then join the groups with AND:
      (synonyms for concept one) AND (synonyms for concept two)
    That shape is also what the ranking reads: each bracketed group counts
    as one concept, so writing a word three ways does not score three times.
"""
