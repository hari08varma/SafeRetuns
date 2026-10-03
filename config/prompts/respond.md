<!-- version: 1 -->
Write the next message to the customer for the SITUATION described in DECISION.
- Use only facts present in DECISION. If DECISION has no amount, do not mention an amount.
- When options are offered, list only those options. When a request is declined, explain the reason in plain words and mention the alternatives given.
- Reply in the language given in DECISION.language, using the same script the customer used.
- At most four short sentences. No headings, no markdown.
Return only a json object: {"message": "<text>"}
