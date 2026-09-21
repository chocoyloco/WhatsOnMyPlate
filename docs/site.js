// Draws the top navigation on every page.
// To add or rename a page, edit this list once — every page updates.
const PAGES = [
  ["home",    "MyHome",    "index.html"],
  ["meal",    "MyMeal",    "meal.html"],
  ["goals",   "MyGoals",   "goals.html"],
  ["account", "MyAccount", "account.html"],
];

const current = document.body.dataset.page;   // set on each page: <body data-page="meal">
const nav = document.createElement("nav");
nav.setAttribute("aria-label", "Main");

PAGES.forEach(([id, label, href]) => {
  const a = document.createElement("a");
  a.href = href;                               // relative link, so it works under /WhatsOnMyPlate/
  a.textContent = label;
  if (id === current) a.setAttribute("aria-current", "page");
  nav.append(a);
});

document.getElementById("site-header").append(nav);
