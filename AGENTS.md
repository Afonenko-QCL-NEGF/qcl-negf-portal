# Portal contributor rules

Read `README.md` and `CONTRIBUTING.md` before changing the portal. Keep HTTP
handling, AiiDA execution, and numerical analysis in their owning components.

## Fixed light theme

The user requires the complete research web interface to use a light theme. Use
light backgrounds for pages, navigation, cards, forms, tables, reports, charts,
dialogs, and loading/error states, with readable text and clear controls. Keep
`color-scheme: light`; do not select a dark theme from browser or operating
system preferences or add a dark-theme alternative without an explicit user
request. Maintain sufficient contrast for text, status labels, borders, and
focus indicators. Apply this rule to new interface elements as well as existing
ones.

Keep theme changes in presentation code. Preserve authentication, plan bytes,
resource admission, provenance, and the distinction between process completion
and scientific acceptance.
