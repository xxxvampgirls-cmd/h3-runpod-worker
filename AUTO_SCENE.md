# AUTO SCENE payload

The worker accepts an optional `input.auto_scene` object:

```json
{
  "enabled": true,
  "visual_prompt": "Natural continuation of the start image...",
  "dialogue": "Speaker 1: ... Speaker 2: ...",
  "ambience": "Room tone, footsteps, clothing movement..."
}
```

The desktop/vision layer should infer this plan from the start image. If the user supplies a short idea, treat it as a constraint rather than a full prompt.

To receive the generated scene prompt, the workflow's positive prompt node should have `_meta.title` containing `H3 Prompt` or `Auto Scene Prompt`. The worker deliberately does not overwrite arbitrary text nodes.
