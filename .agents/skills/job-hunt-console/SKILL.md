---
name: job-hunt-console
description: Collect or reuse local job postings, prepare semantic judging tasks, match requirements against a traceable experience archive, and build a static application tracker and selected-job resume drafts. Use for job filtering, evidence-based matching, or a local job application console. Never submit applications.
---

# Job Hunt Console

Read `references/spec.md` before operating. It defines the public workflow, data contracts and scoring boundaries. User instructions govern actual job preferences, model selection and fact confirmation.

## Non-negotiable rules

- Never submit an application. The HTML console has no timers, background network requests or automatic navigation.
- Only user operations change application status. Missing postings are hints, never proof that recruiting stopped.
- Run one crawl round, then stop and diagnose. Retry only approved missing IDs, at most once. Never automatically rerun a full crawl.
- Stop immediately on risk codes `31` / `37`, rate limits, environment anomalies or expired login. Do not probe again.
- Keep personal data, raw JDs, logs and generated pages in a separate local workspace, outside public examples.
- Resolve paths and role vocabulary from configuration. Do not hard-code personal paths, job-search directions or judging models into scripts.
- Scripts create task packages and validate result files. They do not call models. Use the model explicitly chosen in `judge.model` or `judge.task_models` in the host environment.
- Unconfirmed or disputed facts cannot become confirmed through automation. Confirm facts only after the user explicitly confirms their content.
- Unknown ability is not zero. Keep source gaps, uncertain matches and unmapped requirements visible.

## Step 0: Initialize

Run `scripts/init_config.py --dir <workspace>` to scaffold the new evidence workflow. Ask for the user's target cities, role and available model, then sample or reuse jobs. Derive proposed title lists and keywords from samples and ask the user to select them; do not invent a list on a blank page.

`templates/config.blank.json` defaults to `scoring.match_engine = evidence`. `--example` uses a fictional `blocks` compatibility example, not a real person's resume. Empty model selection must be resolved before generating model tasks.

## Step 1: Reuse or collect jobs

Reuse local jobs when possible. Optional crawling requires a separately installed compatible scraper configured in `crawl.scraper`; see the repository's `THIRD_PARTY_NOTICES.md`.

`scripts/daily.py --config <config>` runs a single crawl and diagnosis, then stops. Review missing items before asking whether the user wants a targeted retry. Do not treat the available jobs as the whole market.

## Step 2: Filter and annotate

`scripts/pipeline.py --config <config> --phase filter` applies explicit exclusions. `scripts/judge_task.py --config <config> --pass annotate` writes the semantic task brief and manifest.

Use the brief verbatim. Judge every required block, looking at the work output rather than the tool used. Preserve the distinction between `说不好` and `非职责`. Write one `judge_verdict_N.json` per batch immediately. Do not compute a posting's final ratio inside the model verdict; `judge_apply.py` validates and recomputes it.

Apply complete results with `scripts/judge_apply.py --config <config> --pass annotate`, then `scripts/pipeline.py --config <config> --phase apply`. Keep existing task inputs while a round is open; only complete missing batches, never regenerate the whole round silently.

Review taxonomy synonyms at least every three crawl rounds or when more than twenty new points accumulate. Merging definitions is a human decision.

## Step 3: Evidence and requirements

Use `scripts/evidence_workflow.py --config <config> <command>`:

- `init` creates separate blank evidence files without overwriting existing ones.
- `sources` indexes only the explicitly selected UTF-8 sources.
- `market-task` / `apply-catalog` prepare and apply a capability catalog.
- `interview-task` / `apply-profile` maintain facts and assessments. Reuse existing materials and ask only missing or contradictory details. `--all-sources` packages all selected source lines for initial review.
- `requirements-task` / `apply-requirements` extract JD requirements and conditions.

Each task supplies a `brief.md`, manifest and result schema. Read them before judging. Never treat interviewer praise or model-written suggestions as things the user has actually done. `apply-profile --confirm-facts` is permitted only for the fact IDs whose content the user has confirmed.

Source hashes and quotes, catalog ownership, task configuration and JD hashes are checked again on apply. Repair invalid references instead of silently resetting abilities or bypassing validation.

## Step 4: Score and build the console

Run `scripts/score.py --config <config>` and `scripts/build_console.py --config <config>` after applying the necessary task results. `daily.py --rebuild` advances one stage using existing data and stops when semantic judging is required.

The evidence engine deduplicates requirements, caps satisfaction, handles AND/OR, displays unknown ranges and keeps critical gaps. Company score and salary stay separate from ability matching. Missing `match_engine` or explicit `blocks` uses the compatibility engine; do not mix its results with evidence scores.

Check user status persistence, explicit links, detail expansion, visible unknowns and excluded rows. User clicks may open source websites, but the generated page makes no background requests. Do not claim a UI check proves semantic match quality.

## Step 5: Selected-job resume

Use `scripts/resume_export.py --config <config> --job-id <id> task`, complete the writing task, then use its `export` subcommand. Run `--help` for exact options.

Only relevant confirmed facts and associated responsibility boundaries may enter the draft. Review wording, numbers, organization and date attribution; valid IDs alone do not prove a paraphrase is truthful. Inspect the resulting PDF before delivery.

The generated `简历编辑器.html` supports local manual editing, backup/import and browser print-to-PDF. Edits do not confirm facts or update the profile. There is no automatic submission.

## Handoff

Report what was implemented, what was checked and what remains unknown. Separate synthetic test outcomes, real-data validation and actual job-search results. Do not publish a user's workspace or cite private historical files in public documentation.
