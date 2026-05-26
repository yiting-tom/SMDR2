## 1. Remove the title link

- [x] 1.1 Delete the `<a class="title" href="/" title="Back to dashboard">SMDR2</a>` element from `app/templates/viewer.html`'s `<header>` block (line 10).

## 2. Decide CSS fate

- [x] 2.1 Grep `app/templates/*.html` for `class="title"`. If the dashboard still uses it, leave `style.css` untouched. If not, delete the `.title` rule from `app/static/style.css` in the same commit.

  → Dashboard still uses `<span class="title">SMDR2</span>`, so `header .title` rule kept. The `header a.title:hover` rule was anchor-specific and the deleted viewer link was the only `<a class="title">` — that rule removed as orphaned.

## 3. Verify

- [x] 3.1 Open the viewer in a browser. Confirm `← Products` is the only back-to-dashboard affordance in the header and that clicking it still lands on `/`.
- [x] 3.2 Open the dashboard. Confirm the brand `SMDR2` text is still present and styled correctly (i.e. the CSS decision in 2.1 didn't break it).
