# Five-minute demo

**One sentence to open with:** "Every claim gets a trust score, a colour and reasons. Agents do the investigating, a person decides, and the system only learns
what a reviewer chooses to teach it, after a safety check."

## Before you start (2 minutes, once)

Use a scratch copy of the dashboard database so your demo decisions do not pollute the real one:

```bash
# macOS, Linux, Git Bash
cp data/app.duckdb data/demo.duckdb
export CLAIM_TRIAGE_APP_DB=data/demo.duckdb
uv run streamlit run src/claim_triage/app/dashboard.py
```

```powershell
# Windows PowerShell
Copy-Item data\app.duckdb data\demo.duckdb
$env:CLAIM_TRIAGE_APP_DB = "data\demo.duckdb"
uv run streamlit run src\claim_triage\app\dashboard.py
```

(You need the setup from the README first: steps 4 and 6, `data.build` and `app.backfill`; the knowledge-base part of the tour also needs your Neo4j, step 5.) Have `docs/images/ARCHITECTURE.png` open in another window.

## The script

| Time | Show | Say |
|---|---|---|
| 0:00 | `docs\images\ARCHITECTURE.png` | "Claim in, a rules engine and graph lookups first, then one investigating agent, a second agent that argues the innocent side, code that computes the score, and a third agent that explains it. Then a human decides." |
| 0:30 | **Review queue** | "380 claims, least trusted first: 140 red, 81 review needed, 159 green. Notice the customer is only an ID: the reviewer cannot identify anyone." Filter to red and health. |
| 1:15 | Select the top row, **Open** | "Trust 0.07. The reason is in plain words: the bill is 3.2 times what comparable hospitals charge." |
| 1:45 | Scroll the **findings** | "Each finding says how confident it is, what could innocently explain it, and which records it rests on." Open a record in **Look at a record** (the provider or a document). "Green is never an approval. A reviewer still decides." |
| 2:30 | **Your decision** | Choose *Change the band*, pick Green, write "discharge summary documents an ICU stay". Save. "I disagreed with the system. That is allowed, and it is recorded for audit." Press **Next claim**. |
| 2:50 | Optional: **Escalate** | On another claim choose *Escalate to someone else*, type a second reviewer's name (for example `senior-1`), write why, tick a tag if you like, Save. "I can hand a claim to a colleague. It stays open and now shows as assigned to them. I cannot escalate to myself." In the queue, set the sidebar name to `senior-1` and switch on **Assigned to me** to show it arrive. |
| 3:00 | **Knowledge base** | "The system can learn from decisions, but nothing is learned silently." Tick the finding from the claim you agreed with, add a note, **Update knowledge base**. "A safety check runs 36 test searches with and without the signal. It passed, so agents can now look it up." |
| 3:40 | Optional: the blocked case | Tick another finding and write a note stuffed with generic words (for example: *claim hospital bill policy customer nominee vehicle accident document date death income insurer payout account provider treatment*). "This one is blocked: it would show up in unrelated searches." |
| 4:10 | **Evaluation** | "Here is the honest picture. Plain rules score 99 to 99.5% on the original clean data and the agents 91 to 93%, on dev and on the held-out set alike, so on easy data you do not need agents. The case for them is the hard cases: rules get 50%, agents 71%, agents with retrieval 100%. The page lists what we have not measured, mainly the larger model on the remaining scenarios and the held-out set." |
| 4:50 | Close | "Next step is the larger model on the remaining cross-policy scenarios and the held-out set, to see whether the gap to the rules closes." |

## Questions you should expect

- **"If rules score 99%, why agents?"** Because rules cannot read a discharge summary or look up a hospital's record of confirmed frauds. The hard cases were built to test exactly that. They show what those abilities are worth; they do not show how often they matter in real life. On the clean original data the rules are better, and the page says so.
- **"Why do the agents score lower on the original data?"** They are more cautious: they never put fraud in green, but 14 to 16 of 70 real frauds went to "review needed" instead of red, mostly on cross-policy claims where that run used the small model. On the two weakest scenarios the larger model lifted the score from 65–78% to 95% (rules 100%), at about 18 times the cost per claim.
- **"What about the earlier 30 out of 30?"** That was a sample we tuned on, so it overstated things. The full dev run and the held-out run, which agree, are the honest numbers.
- **"Can the AI approve or reject a claim?"** No. The score is plain arithmetic over findings, and a person decides every claim. The agents cannot set a score.
- **"What stops a bad lesson entering the knowledge base?"** A reviewer has to tick it and write why; it must pass the content checks and the 36 test searches; it can never change a score. What the checks cannot tell us yet is whether a lesson improves agent decisions.
- **"What about privacy?"** Names, phones, accounts and addresses live in a separate vault the dashboard never opens. Text is scrubbed, a canary scan looks for any leaked value, and locations are generalised for small groups. One leak (old surnames in claim text) was found and fixed during the build.
- **"What would you do next?"** A three-run stability check, the larger model on the remaining cross-policy scenarios and the held-out set, a judge model for explanation quality, a fairness check for the agents and, with real claims, a pilot.

## After the demo

```bash
rm data/demo.duckdb          # PowerShell: Remove-Item data\demo.duckdb
```
