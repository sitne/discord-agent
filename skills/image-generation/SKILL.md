---
name: image-generation
description: Generate images using free APIs like Pollinations.ai. Use when the user asks to create, draw, generate, or make an image, picture, illustration, icon, logo, or diagram.
keywords: [image, picture, photo, generate, draw, illustration, art, visual, icon, logo, diagram, 画像, 写真, 絵, イラスト, 生成, 描いて, アイコン, ロゴ]
---

# Image Generation

## Pollinations.ai (Free, no API key)

Generate images via URL:
```
GET https://image.pollinations.ai/prompt/{description}
```

Parameters (query string):
- `width`: image width (default 1024)
- `height`: image height (default 1024)
- `seed`: random seed for reproducibility
- `model`: flux, turbo (default: flux)
- `nologo`: true to remove watermark
- `enhance`: true to enhance prompt

Example with http_request:
```json
{
  "method": "GET",
  "url": "https://image.pollinations.ai/prompt/a%20cute%20cat%20in%20space?width=1024&height=1024&nologo=true"
}
```

The response is the image binary. To share in Discord, send the URL directly — Discord will embed it:
```
https://image.pollinations.ai/prompt/a%20cute%20cat%20in%20space?width=1024&height=1024&nologo=true
```

Tips:
- Describe in English for best results
- Be specific: art style, colors, composition
- URL-encode the prompt (spaces → %20)
- For icons/logos, add "minimal, flat design, vector style" to the prompt
- For diagrams, consider using text-based diagrams (Mermaid) instead
