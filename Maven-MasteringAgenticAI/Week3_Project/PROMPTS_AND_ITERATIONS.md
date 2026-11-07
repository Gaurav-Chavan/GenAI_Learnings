# Prompts, iterations and learnings (source for the Week 3 Google Doc)

The handout asks for: project overview, datasets used, prompts used while vibe coding,
iterations tried, and learnings. Log them here as we go, so the doc is just a rewrite.

## Project
Project 3A, Market Research Agent: Competitor Analysis. Code track (LangChain + LangGraph),
Tavily as primary search, Streamlit UI. Sample company: **Cursor** (chosen because it has
conflicting pricing sources, fast-moving news and an ambiguous "top 3 competitors").

## Datasets / data sources
Live web and news via Tavily Search / Extract. No static dataset.

## Prompts (vibe coding), in order
1. Asked for an approach to Project 3A using the handout's Part 2 framework (the one-liner
   and the 9-field framework) on the LangChain + LangGraph track.
2. Asked which companies to consider as samples -> shortlist built around what each tests
   (clean path / fast-moving news / sparse pricing).
3. Asked to think through Cursor -> ran live web searches to see what the sources disagree on.
4. "Proceed on this use-case" -> scaffolded the repo and a stub-data LangGraph skeleton.

## Iterations
| # | What we tried | What happened | What we changed |
|---|---|---|---|
| 1 | Skeleton: supervisor graph, stub nodes, 2 `interrupt()` checkpoints, SqliteSaver-ready | Tests failed: checkpoint 1 never advanced | `Command(resume={})` is ignored (empty dict = "no value"). Resume payloads now always carry `{"approved": ...}` |
| 2 | Retry loop log | Retried gather was logged as round 0 | Round is now computed from the *next* retry count, not the state before the update |
| 3 | Asked "why not uv?" | No good reason; uv was already installed | Replaced requirements.txt and the pip venv with pyproject.toml + uv.lock |
| 4 | Chose models for the Analyst | The suggested Llama-3.3-70B ID was not in the account's Nebius model list (31 models) | Listed the real IDs through the models endpoint and switched to `Qwen/Qwen3-235B-A22B-Instruct-2507`. Lesson: verify model IDs against the live list, not docs |
| 5 | Wired real Tavily into `tools.py` | Live call failed with `CERTIFICATE_VERIFY_FAILED` (the Avast issue again, this time inside Python's `requests`) | `truststore.inject_into_ssl()` in `config.py` so all HTTP clients use the OS certificate store, with verification still on. Tool errors are now split into transient (retried) and permanent (fail fast) |
| 6 | Built the real Discovery agent: 2 Tavily searches, then Claude structured output, then code validation (grounded in the evidence, no self-reference, no duplicates, exactly N), then one repair attempt | Worked first time on Cursor, but langchain-anthropic warned that forced tool calling is unsupported on Claude Sonnet 5.5 and can silently not fire | Switched to `with_structured_output(..., method="json_schema")` (native structured output). A test now asserts the method |
| 7 | Built real Gather (per-competitor, finds the official pricing page, retry rounds target only the missing fields and merge with earlier evidence) and Analyst (Nebius Qwen, native JSON-schema output, then code assigns each fact's trust status) | First offline run: one test failed because I had miscounted calls in the scenario (no pricing page found means no extract call). The code was right, the test expectation was wrong | Fixed the test. Live runs on GitHub Copilot and Devin Desktop both worked first time |
| 8 | Built the real Critic (deterministic gap and warning rules) and Writer (code renders every fact with status label and link; the LLM writes only summary, table cells and implications, and any dollar figure not in the facts is dropped; human revisions are accepted only if they add no new figure or URL). First full live run on Cursor | Worked end to end but took **602 s**, and Claude Code's profile was wiped: the Qwen call ran away to the 8192-token limit in round 2, and my fallback overwrote the good round-1 profile with an empty one | (a) keep the previous profile if re-analysis fails, (b) cap output at 3500 tokens, bound the evidence to the best 8 web / 6 news results, and retry once with less evidence, (c) filter YouTube/Reddit/etc., (d) strip prompt tags such as `[OFFICIAL PAGE]` from notes |
| 9 | Re-ran the full graph with per-node timing | **79 s** (was 602 s): the runaway generations were the time sink. 15 Tavily calls, 2 retry rounds, 2 Writer lines dropped by the grounding check | Table cells that fail the check now fall back to the real price text, and the log names the rejected figures |
| 10 | Fix official-site detection: Discovery now returns each competitor's official website, and "first party" means "on that site's domain" | Attempt 1 required the domain to appear in Discovery's evidence, and **dropped every correct URL** (github.com, windsurf.com, claude.com): the evidence is roundups *about Cursor*, so rivals' own domains rarely appear. Attempt 2 verified the claim against the competitor's own search results, which still failed for Windsurf because SEO blogs crowd the official domain out of pricing searches | Final rule: the website is only a *claim* until Gather verifies it, either (a) the competitor's own search results include that domain, or (b) fetching `<site>/pricing` returns a page on that domain that mentions the product. Verified site is stored in the raw evidence and used for pricing-page lookup and `confirmed` status. A claim that fails is not retried. 7 new offline tests (84 total). Live Cursor run: Claude Code pricing is now `confirmed` from claude.com/pricing in round 0; run time 53 s, 17 Tavily calls, 1 retry round (was 85 s, 17 calls, 2 retry rounds with third-party-only pricing) |
| 11 | Built the Streamlit UI as a thin view over a `ResearchSession` controller (phases: new, awaiting_competitors, awaiting_briefing, failed, done), SQLite checkpointing, run id in the URL | Stub run worked. **First live run through the UI showed Streamlit rendering `$...$` as LaTeX**: "Free tiers are $0 ... range from $10/mo ... $200/mo" came out as garbled code, in a briefing whose whole point is prices. Also, while a step ran, the old checkpoint form stayed on screen above the progress log, which looked editable | Escape `$` for display only (the saved file is untouched). Button clicks now queue the step and rerun, so the stale form is never drawn during a run. 11 new tests: controller phases and recovery with a failing node, plus the page driven headlessly through both checkpoints, discard, revise, the `$` escape and the failure/retry screen (95 total) |
| 12 | Pointed the UI at a company outside software (P&G), then re-ran Cursor live through the UI to capture screenshots | **P&G crashed the Gather step**: when Tavily cannot fetch *any* requested page it returns an error *string* instead of a dict, and P&G has no `/pricing` page for the site probe to fetch (`'str' object has no attribute 'get'`). The Cursor re-run then showed three more flaws: the Writer's price-figure check read "Free $0, Pro $20" as the figure "$0," (a trailing comma) and rejected valid lines, the fallback table cell was cut mid-word ("Enterprise L"), and the briefing column was too narrow for the comparison table | A string result from `tavily_extract` is now "nothing extracted" (or classified as an error if it says something else); the dollar-figure pattern no longer swallows a trailing comma or full stop; fallback cells are cut at a plan boundary with "…"; briefing column widened (7:3). 4 new tests (101 total). Live check: P&G now runs end to end (63 s, 23 search calls, both retry rounds spent chasing prices that do not exist) and the briefing says pricing was not found. The first version of this fix was lost when the command that wrote it was interrupted, and it only came to light because the live re-run still crashed. Live Revise (never run live before) worked: the summary went from five bullets to the three requested, with no new figures |

## Prompts inside the agent
All LLM prompts live in `src/competitor_agent/prompts.py`: the competitor definition, the
discovery system prompt (resolve the company, pick N competitors, use only the evidence, cite
evidence URLs) and the repair prompt (lists the concrete validation problems).

## Learnings / observations
- **Discovery result for Cursor (2026-11-05):** GitHub Copilot, Devin Desktop (described by
  sources as "formerly Windsurf") and Claude Code, each with 3 evidence URLs. This matches the
  mix the competitor definition asks for: market leader, closest like-for-like, fast-rising
  challenger. The Windsurf rename comes from blog sources, so it is a good example of something
  the human checkpoint or the Critic should confirm.
- **Live Gather + Analyst (2026-11-05).** GitHub Copilot: official plans page found and
  extracted, 7 tiers with prices, `confirmed`; the Analyst noted a conflict between a blog's
  AI-credit figure and the official docs and trusted the docs. Devin Desktop: official
  `devin.ai/pricing` found, Free $0 / Pro $20 / Max $200 / Teams $80 + $40 per seat,
  `confirmed`; it noted that TrustRadius omits the Teams structure. Cost: 4 Tavily calls and
  one Qwen call per competitor.
- **The model never decides how much to trust a fact.** The Analyst prompt asks for a value and
  a source URL; code then checks the URL was really fetched and whether the domain is the
  company's own (`confirmed`) or a third party (`reported`). A made-up URL becomes `unverified`.
- **Weak spots seen:** positioning for Devin Desktop came from a Substack post quoting the
  tagline (`reported`), and one "news" item was a low-quality aggregator page that mixed old
  and new events. A source-quality rule in the Critic or Writer should handle both.
- **A degraded fallback must never overwrite good data.** The first full run lost Claude Code's
  working profile because the error path wrote an empty one. Error handling needs a test for
  "what was already there".
- **Cap model output.** One uncapped structured-output call generating 8k tokens, twice, took
  about 8 of the 10 minutes. `max_tokens`, bounded prompts and a timeout fixed it (602 s to 79 s).
- **Official-site detection (fixed in iteration 10).** "Is this URL the company's own site?"
  first matched words from the product name, which failed for Claude Code (made by Anthropic)
  and cost 2 wasted retry rounds. Now Discovery proposes each competitor's website and Gather
  verifies it. Lesson: **a check must be tested against the real case it is meant to fix.** My
  first verification rule looked sensible but rejected all three correct answers, because I
  had assumed the evidence would mention the competitors' own domains.
- **A model's claim is a candidate, not a fact.** The model gets the official website right
  (3 of 3 here) but we still do not label anything `confirmed` on its word; the page itself
  must be fetched and mention the product.
- **Test the UI with real data, not only stubs.** Stub data has `$20/mo`, one price per line, so
  nothing looked wrong. Real output with two prices in one sentence exposed the LaTeX problem
  that no offline test would have found. Any text a model writes about money needs a check of
  how it is *displayed*, not just whether the numbers are grounded.
- **Keep the UI free of graph knowledge.** A controller that exposes phases and three verbs
  (start, resume, retry) made the failure path testable without a browser: the same
  "failed node, then retry from checkpoint" test runs on the controller and on the page.
- **Read the log lines you wrote for yourself.** The "figures not in the facts: ['$0,']" line had
  been in the run log since iteration 9. Its odd shape was the clue that the guard itself was
  wrong. A guardrail that rejects good output is as costly as one that lets bad output through,
  so its false positives need logging and a test.
- **Third-party SDKs return failures as values, not only as exceptions.** `TavilyExtract` returns
  a plain string when nothing could be fetched. Check the *type* of what comes back from every
  tool, not just whether it raised.
- **Not every company has a price list.** P&G exposed that the pipeline assumes software-style
  pricing (a pricing page, plans, per-month figures). It no longer crashes, but a consumer-goods
  company will still show "pricing not found". Product-fit is a limit to state, not hide.
- **Still open:** Windsurf's probe of `windsurf.com/pricing` did not verify in round 0 (the
  official page was only picked up in round 1 via search), most likely because the rebrand to
  Devin Desktop changes the domain the page lands on. Also the name "Windsurf" pulls in
  windsurfing-sport news; news queries need the product category added.
- **Output varies between runs.** GitHub Copilot's positioning was cited from the official
  docs in one run and from a blog in the next, because the model picks among valid sources.
- **Let the LLM propose, let code check.** The model can still hallucinate a plausible name, so
  plain code checks grounding, duplicates and count, and the human checkpoint gets a note when
  the check fails.
- **Resume with an empty dict silently re-interrupts.** Always send a non-empty payload.
- **Web sources disagree.** Live search for Cursor returned conflicting plan names and
  prices (blogs dated March to October 2026) and an unconfirmed acquisition claim only on
  aggregator sites. Design consequences: official-page-first pricing, an as-of date on every
  fact, and a fact status (confirmed / reported / unverified / not_found).
- **Ground truth beat the blogs (Cursor, 2026-11-05).** `tavily_extract` on cursor.com/pricing
  showed the official plans are Free $0, Plus $8, Premium $30, Super $100 per month. The
  $20/$60/$200 "Pro/Pro+/Ultra" prices in many blog posts are stale. Tavily news also showed
  SpaceX completed a ~$60B acquisition of Cursor (Oct 2026), which my earlier search had only
  seen as an unconfirmed option. Both support the design: official page first, as-of dates,
  and fact status labels.
- **Raw extracts are noisy.** The pricing page text includes unrelated bundle features and
  navigation, so the Analyst must extract with a schema and not copy text.
- **Date formats differ.** Tavily general results have no date; news dates are RFC-1123
  strings. Normalize to ISO dates before the Critic checks freshness.
- **Supervisor pattern.** One orchestrator node that looks at the step that just finished and
  routes with `Command(goto=...)` (including `Send` for parallel fan-out) keeps control flow
  in one readable place, and gives clear spots for LLM decisions.
- **Environment:** `pip` failed with `CERTIFICATE_VERIFY_FAILED` because Avast's HTTPS scanning
  re-signs traffic. First fix: point `PIP_CERT` at pip's certificate bundle plus Avast's
  root certificate, without disabling verification.
- **Packaging (iteration 3):** I first scaffolded with `venv` + `pip` out of habit, without
  checking for uv. Migrated to uv (`pyproject.toml`, `uv.lock`, `.python-version`, `uv run`)
  for reproducible installs. `uv sync --system-certs` also fixed the Avast problem by using the
  Windows certificate store, so the hand-built bundle was unnecessary.
