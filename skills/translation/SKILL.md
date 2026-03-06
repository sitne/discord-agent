---
name: translation
description: Translate text between languages using free APIs. Use when the user asks to translate, convert between languages, or needs localization.
keywords: [translate, translation, language, convert, english, japanese, spanish, french, chinese, korean, localize, i18n, 翻訳, 訳して, 英訳, 和訳, 中国語, 韓国語, フランス語, スペイン語]
---

# Translation

## MyMemory API (Free, no API key for basic usage)

Translate text via GET request:
```
GET https://api.mymemory.translated.net/get?q={text}&langpair={from}|{to}
```

Language codes: `en` (English), `ja` (Japanese), `es` (Spanish), `fr` (French), `de` (German), `zh-CN` (Chinese Simplified), `ko` (Korean), `pt` (Portuguese), `it` (Italian), `ru` (Russian).

Example — English to Japanese:
```json
{
  "method": "GET",
  "url": "https://api.mymemory.translated.net/get?q=Hello%20world&langpair=en|ja"
}
```

Response structure:
```json
{
  "responseStatus": 200,
  "responseData": {
    "translatedText": "こんにちは世界",
    "match": 0.95
  }
}
```

Extract `responseData.translatedText` from the JSON response.

## Limits & tips
- Free tier: 5000 chars/day without email, 50000 chars/day with email parameter `&de=your@email.com`
- For long texts, split into chunks under 500 characters for best quality
- URL-encode the query text (`q` parameter)
- If the source language is unknown, use `autodetect` as the `from` code: `langpair=autodetect|en`
- Always report both the translation and the detected source language to the user

## DeepL API (requires API key)

If a DeepL API key is available in environment (`DEEPL_API_KEY`):
```json
{
  "method": "POST",
  "url": "https://api-free.deepl.com/v2/translate",
  "headers": {"Authorization": "DeepL-Auth-Key YOUR_KEY", "Content-Type": "application/json"},
  "body": {"text": ["Hello world"], "target_lang": "JA"}
}
```

DeepL language codes are uppercase: `EN`, `JA`, `ES`, `FR`, `DE`, `ZH`, `KO`, `PT-BR`, `IT`, `RU`.
