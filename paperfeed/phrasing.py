"""Small wording helpers, so counts read like English.

"published 1 days ago" is the kind of thing that makes a tool feel
unfinished, and it appears wherever a number is glued to a noun. One helper
used everywhere is easier to keep right than five separate f-strings.
"""


def count(number, noun, plural_form=None):
    """'1 paper', '3 papers', '0 papers'."""
    if number == 1:
        return "1 %s" % noun
    return "%d %s" % (number, plural_form or noun + "s")


def days_ago(days):
    """'today', 'yesterday', '3 days ago'."""
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return "%d days ago" % days
