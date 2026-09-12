# Presentations, narration, and portable media

Use this path when the deliverable includes visual communication, audio, video,
or a presentation package. Select the formats the user needs; a project does not
automatically require every export in this example.

## Reusable division of work

The successful OpenWhispr workflow used the coordinator for source preparation,
integration, rendering, and QA; Claude Code for structured slide content and the
speaker script; Antigravity for art direction; NotebookLM for an alternate visual
deck, mind map, and conversational audio; and Piper for exact-script local speech.
This is one proven arrangement, not a rule binding those roles to those tools.

Make a shared brief with audience, story, scope, key factual distinctions, visual
tone, and current evidence. Ask a content worker for structured slide/scene data,
not an unreviewed final binary. Ask the design worker for a coherent visual system
and meaningful diagrams. Keep one integrator responsible for layout and exports.

Review narrative and diagrams against the source before final narration. Avoid
inventing metrics, exact commands, or live-demo behavior. Carry status labels
such as implemented, documented, illustrative, and proposed into the final work
where a reader needs them to interpret a claim.

## Audio is an artifact

For exact narration, freeze the reviewed script and synthesize one clip per slide
or scene using an available authorized voice tool. Save a timing manifest and
align video scenes to the measured clip durations. Piper was useful here, but it
is replaceable by another suitable TTS engine. Do not play audio on the user's
computer during production unless requested; mute automated playback validation.

The recipient should not need a TTS engine: include the resulting MP3/WAV clips
and, when requested, a combined MP3 and narrated MP4. Say explicitly which exports
contain narration; audio in an HTML deck or MP4 does not imply embedded PPTX audio.

NotebookLM's conversational overview is a separate generated interpretation.
Inspect a transcript, identify unsupported claims, and label uncorrected drafts.
Do not use a visually impressive alternate artifact to override factual review.

## Render and validate

Use the relevant presentation/document/media skill when available. Inspect the
actual rendered output: slide counts, clipping, overlaps, typography, notes,
contrast, mobile/browser controls, and useful diagram labels. Open editable files
in their intended application when practical. Verify media decodes, durations are
plausible, and sample frames match the expected narration scenes.

Automated geometry and muted playback checks establish rendering/playback
behavior, not a human listening review or a live demonstration of the product.
Describe those distinctions only where they affect the delivery's claims.

## Portable package

When asked to share a runnable presentation:

- Include `START-HERE.html` or a comparably clear entry point with playback and
  download links. A simple Windows launcher can open it; Mac/Linux users can open
  the HTML directly. No server is needed when the content can be fully static.
- Keep images, fonts where licensing allows, scripts, video, and narration local.
  Use relative references. Avoid remote CDNs, private notebook links, account
  dependencies, or producer-machine paths in required playback resources.
- Include an accessible local export of the mind map if it otherwise requires
  the owner's account. Keep the original data and identify its provenance.
- Put recipient instructions next to the entry point: extract the whole archive,
  keep the folder together, open in a supported browser, click Play/Narrate.
  Explain that voice is prerecorded and no Piper/model installation is needed.
- Export only requested useful formats, omit redundant production intermediates,
  and preserve correction notes for any experimental artifacts included.
- ZIP the package, check archive integrity, extract into another directory whose
  name contains spaces, and test the extracted entry point with network disabled.
  Check relative links, navigation, narration, and video through that copy.

Keep raw worker output, prompts, and build dependencies out of the recipient
package unless requested as source material. Delivery should explain the project;
the orchestration ledger belongs with production records.
