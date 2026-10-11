# CLAUDE.md - Hackathon Development Directives

## CRITICAL REPOSITORY & GIT RULES

- **STRICT GIT LOCK**: You MUST NEVER execute `git commit`, `git push`, `git rebase`, or `git remote` commands under any circumstances.
- **USER-ONLY COMMITS**: Whenever code changes or feature completions are made, provide the exact `git add` and `git commit -m "..."` commands in your output for the user to execute manually.

## HACKATHON CONTEXT & JUDGING ALIGNMENT

- **Event**: Environmental Hacks 2026 (Bharat Builds Tour)
- **Track**: Track 03 — Waste and Energy (Primary Focus: Energy Efficiency, Clean Power, Solar Optimization, Smart Consumption)
- **Submission Requirements**:
  1. Working Execution (1 rock-solid, fully functioning core flow > 5 half-working features).
  2. Mandatory AWS Integration (Must use AWS Open Source tools like SAM/LocalStack or AWS Services like SDK, S3, DynamoDB, Lambda, SageMaker/Bedrock).
  3. Clear Design & Usability (Intuitive UI/UX, instantly clear for judges in a 3-minute video).
  4. Explicit AWS Documentation (AWS architecture must be explicitly explained in code and `README.md`).

## DEVELOPMENT WORKFLOW

1. **Code Execution & Edits**:
   - Implement clean, production-ready backend and frontend changes directly in local files.
   - Prioritize fixing broken endpoints, enhancing UI reactivity, and strengthening the primary energy domain workflow.
2. **AWS Feature Integration**:
   - Ensure the app explicitly consumes an AWS service (or local AWS SDK mock via LocalStack/SAM CLI).
   - Store energy metrics/telemetry in AWS DynamoDB/S3 or process solar/grid optimization requests via AWS Lambda / SageMaker logic.
3. **Completion Deliverable**:
   - When finishing any code task, output the status, test command, and exact Git commands for the user to commit.
