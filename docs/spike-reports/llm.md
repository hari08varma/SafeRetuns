# LLM spike report (DeepSeek-V4.1-Flash)

**Status: NOT RUN YET.** The DeepSeek API is not reachable from the build environment and no API key was available.

Run it on a machine with access:

```bash
cp .env.example services/.env      # set DEEPSEEK_API_KEY
make spike-llm
```

This file is overwritten with the results (basic chat, JSON output, tool calling, image input, thinking mode) and a GO / NO-GO verdict.
