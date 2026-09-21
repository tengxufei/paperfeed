# Start msg

```

We're building a tool called PaperFeed. I'm a researcher, not a strong coder: explain choices in plain language and keep the design as simple as it can be while still working.

WHAT PAPERFEED IS
A literature alert tool: I give it keywords, it finds recent matching papers, and emails me the new ones on a schedule.

FEATURES TO PLAN
- Keyword sets I can easily add / edit / remove.
- Fetch recent papers, needs to cover PubMed + preprints. Deduplicate across sources by DOI.
- "New since last time": remember what it has already shown me so each paper appears once, across runs.
- Runs on a schedule: every 3 days by default, but I can change the interval myself.
- Email me a readable digest.
- ALWAYS write a local digest file too (HTML or markdown), so if email fails I still see the results. Treat that file as the thing we check to verify it worked.

HARD CONSTRAINTS
- Python, standard library + `requests` only.
- Config-driven: all settings I might change — keywords, the schedule interval, and email settings — live in a config file I can edit, not hardcoded, so any researcher could reuse this by editing config.

Give me a plan first and wait for my approval before writing any code.


```


```
Before we add anything: git init if needed, and commit the current working PaperFeed as our baseline so we can always roll back.

Now I want to make it genuinely more useful — right now it's too plain. This is a PLAN session: do NOT write code yet.

First, look at what PaperFeed currently does and propose a menu of improvements that would make it more useful to a researcher — grouped as:
  (a) improving what's already here (e.g. better relevance ordering, cleaner digest, filtering by date/journal), and
  (b) genuinely new capabilities.
For each idea: one line on what it does, why a researcher would want it, and roughly how hard it is. Flag anything that would need a paid AI API vs. what stays free/keyless.

Don't build. Give me the menu, I'll pick, then you plan only the ones I choose and wait for my approval.



```

```
All "(a) Improving what's here" you mentioned; For b1, only keep papers that user selected; b8 should be a digest of research published (or preprint) in recent 2 or 3 months that reflect the research trend; also do b2 and b7 and b10 (is there more efficient way to read on phone?). For those need API key, just let user type in then the tool should be able to work unless they provide wrong key
```





