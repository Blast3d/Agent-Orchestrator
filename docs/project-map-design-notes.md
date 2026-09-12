# Project stories visual design

The page makes the contribution audit readable without exposing the underlying
account, model, token, or file details. It is a self-contained local HTML view;
it does not load fonts, libraries, analytics, or other network resources.

## Visual language

- A warm white workspace sits inside a dark navy project library. Restrained
  teal, violet, and warm earth accents distinguish agent identities.
- The project sits at the center of a mind map. Equal-size cards and identical
  connecting lines show who helped; neither card size nor line weight implies
  effort, traffic, or an additional measurement.
- Each card's ring represents its recorded share of finished work. Missing
  shares get an empty, dashed ring and the words `Share not recorded`.
- Agent identities remain separate, including separate Codex participants.
  Provider labels sit underneath their names. Color is stable for a given
  identity across projects.
- Areas of work filter the rings. Selecting an agent opens a plain-language
  account of what helped finish the project. Usage counts appear quietly in
  the details and are explicitly separate from contribution shares.
- The comparison view uses a fixed 100% scale. Known values are shown at their
  supplied widths. Unassigned shares are striped; missing shares are never
  converted to zero or used to rescale the known values.

## Interaction and accessibility

- Project cards and agent cards are actual buttons. View tabs support the arrow,
  Home, and End keys; selections and changes are announced to screen readers.
- Contribution details have a visible close control. Escape closes them and
  returns focus to the selected agent.
- Narrow screens and larger teams use a flowing card arrangement to keep names
  readable and prevent the map from becoming crowded.
- Reduced-motion preferences are respected. Keyboard focus has a visible ring.
- Save picture creates a standalone SVG with escaped text, using browser-native
  drawing. Save / print uses the browser's print controls.

## Implementation boundary

`app/assets/project-map.html` accepts the catalog supplied by the Python
application. All report-provided text is inserted with DOM text operations;
none is parsed as HTML. The page does not read arbitrary files, make model
calls, or change projects. The generated catalog is a saved view, with its
date shown at the bottom.

Grok's design review informed the equal-sized cards, dashed unknown shares,
explicit close/focus behavior, and the distinction between usage and accepted
work. Suggestions to treat attempts as extra drafts or combine every Codex
identity into one were not applied because the available records do not
support those interpretations.
