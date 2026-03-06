---
name: remotion-video
description: Generate videos using Remotion with codegen and GitHub Actions. Use when the user asks to create a video, animation, motion graphic, slideshow, or render an MP4.
keywords: [video, animation, motion, clip, render, remotion, movie, mp4, recording, slideshow, 動画, アニメーション, ムービー, レンダリング, スライドショー]
---

# Video Generation with Remotion

Generate short videos by scaffolding a Remotion project, writing the composition in React/TypeScript, and rendering via GitHub Actions.

## Workflow

1. **Scaffold project** (if not already present):
```bash
npx create-video@latest my-video --template blank
cd my-video && npm install
```

2. **Write composition** — create/edit `src/Composition.tsx`:
```tsx
import { AbsoluteFill, useCurrentFrame, interpolate, Img } from "remotion";

export const MyVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: "#111", justifyContent: "center", alignItems: "center" }}>
      <h1 style={{ color: "white", fontSize: 80, opacity }}>Hello World</h1>
    </AbsoluteFill>
  );
};
```

3. **Register in `src/Root.tsx`**:
```tsx
import { Composition } from "remotion";
import { MyVideo } from "./Composition";

export const RemotionRoot: React.FC = () => (
  <Composition id="MyVideo" component={MyVideo}
    durationInFrames={90} fps={30} width={1920} height={1080} />
);
```

4. **Render locally** (if ffmpeg available):
```bash
npx remotion render MyVideo out/video.mp4
```

5. **Render via GitHub Actions** (for heavy rendering):

Create `.github/workflows/render.yml`:
```yaml
name: Render Video
on: workflow_dispatch
jobs:
  render:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci
      - run: npx remotion render MyVideo out/video.mp4
      - uses: actions/upload-artifact@v4
        with:
          name: video
          path: out/video.mp4
```

Trigger with: `gh workflow run render.yml`

## Useful Remotion patterns

- **Fade in/out**: `interpolate(frame, [start, end], [0, 1])`
- **Sequences**: `<Sequence from={30} durationInFrames={60}>...</Sequence>`
- **Images**: `<Img src={staticFile("image.png")} />` (put files in `public/`)
- **Audio**: `<Audio src={staticFile("music.mp3")} />`
- **Spring animation**: `spring({ frame, fps: 30, config: { damping: 10 } })`

## Tips
- Keep compositions short (3–15 seconds) for fast renders
- 30 fps is sufficient for most use cases
- 1280×720 renders faster than 1920×1080 if quality isn't critical
- Upload the rendered MP4 as a Discord attachment or to a file host
