<!-- version: 1 -->
The customer wants to return something. CANDIDATES lists the items they bought. Pick the one item the conversation refers to. Return only a json object: {"item_id": "<id from CANDIDATES or null>", "confidence": <0 to 1>}. Use null if it is not clear which single item they mean.
