<!-- version: 1 -->
Read the conversation and extract what the customer wants. Return only a json object with these fields:
- "reason_category": one of "size_fit", "damaged", "defective", "wrong_item", "not_as_described", "changed_mind", "other", or null if the customer has not said why.
- "desired_resolution": one of "refund", "exchange", "replacement", "store_credit", or null if not stated.
- "exchange_variant": the size or colour they want instead (for example "L" or "black"), or null.
- "language": the language the customer writes in, for example "en", "hi", "hi-Latn" (Hindi in Latin script / Hinglish), "ta", "te".
- "sentiment": "calm", "frustrated" or "angry".
- "wants_human": true if they ask for a person.
- "legal_threat": true if they mention legal action, a consumer court or a complaint to authorities.

Example: {"reason_category": "size_fit", "desired_resolution": "exchange", "exchange_variant": "L", "language": "en", "sentiment": "calm", "wants_human": false, "legal_threat": false}
