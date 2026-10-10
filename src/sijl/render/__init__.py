"""Renderers that read one `Report` and its `Insights` (Markdown through Jinja2 templates).

`safety` is what a template can see and do; `context` turns a report into the values templates
read; `markdown` renders them with the built-in templates in `templates/`, or with the user's own
(`--template`). The terminal view and JSON/CSV will read the same model.
"""
