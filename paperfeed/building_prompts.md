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
We're building a research tool. I'm a researcher, not a strong coder: explain choices in plain language and keep the design as simple as it can be while still working.

What it is. I give it the topics I follow, and it tells me when new work appears in them. That alerting is the job it exists for and everything else is built on top: it also keeps the papers it finds and helps me analyse what my field is doing. But if it never tells me about a new paper, nothing else it does matters.

Where I want to end up, so you can design toward it rather than bolting it on later:

a paper collection that keeps growing on its own,
a visual dashboard I actually read,
real data analysis over the collection: what is rising, what connects to what, who is publishing, how a period compares to another,
AI that helps me make sense of all that: explaining a paper, reading the trends back to me, and answering questions about my own work from the papers I have kept.
Build it in this order, and stop after each for me to look at it:

Alert me. The core job. Fetch papers matching my topics from PubMed and the preprint servers, deduplicate by DOI, and remember what I have already been shown so the same paper never appears twice. On a schedule I set in the config, deliver what is new to me — email it, and also write a local HTML digest I can open. Then set the schedule up so it runs by itself without me starting it.

Done when: I change nothing, wait for one interval, and a list of genuinely new papers on my topics reaches me without my asking. Not "the file exists if I go and look for it" — it has to reach me. Show me this working before moving on.

Keep what is worth reading. Two separate things, and I want both:

I save papers myself. Wherever I am reading the list — the digest page, the email — I want a single action on each paper that means "keep this, it is worth reading". One click, next to the paper. Not copying an identifier into a terminal.
It also keeps the strongest matches on its own, so the collection still grows in the weeks I do not look at it.
Keep those two apart, so I can always tell which papers I chose and which arrived by themselves. And let me see what I have saved but not yet read, so the pile does not become something I never revisit.

Show. A dashboard over the collection, rebuilt on every run. Charts, not a table of numbers — I want to see the shape of things at a glance. Build one per topic as well as one overall: I follow several fields and a change in one is invisible in a combined total.

Two layers here. The charts are arithmetic over papers already fetched, so they should cost nothing and render instantly — I open this every week. On top of them, use AI to read the charts back to me: what changed, what it might mean, what I should look at first. That interpretation is a large part of why the dashboard is worth having.

Analyse. Let me search any date range I choose, not only recent days, and compare two periods against each other.

One rule: looking back at an old period must not change what the tool considers new. If I examine last year, next week's alert must be unaffected.

Stages 2 to 4 are worth nothing if stage 1 has stopped working, so any change to them has to leave the alerting intact.

Hard constraints

Python, standard library + requests only. No chart library, no web framework, no AI SDK.
Config-driven: everything I might change lives in one config file I edit.
Always write the local file before attempting anything that can fail (email, network, AI). That file is how we check it worked.
Secrets come from environment variables, never from a file in the project.
AI is part of the analysis, not a bolt-on. Design for it from the start: summarising, interpreting trends, answering questions about my own work from the papers I kept. Let me choose the provider in config (Anthropic, OpenAI-compatible, Gemini) and read the key from an environment variable.
But it must never be a single point of failure. If the key is missing, wrong, or the service is down, everything that does not need it still works and the tool says what happened. Never let a failed AI call cost me a digest or a dashboard.
How I want you to work

Plan first and wait for my approval before writing code.
Verify by running it and opening the output, not by reading the code back to me. Tell me what you actually observed.
When you find a bug, say what it was and how you proved it is fixed.
Do not quote API prices or model names from memory. Check them.
If you are unsure, say so rather than guessing.
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





