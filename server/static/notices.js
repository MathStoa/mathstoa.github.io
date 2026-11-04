// Lets a visitor close flash messages such as "Successfully signed in as …".
// The buttons are hidden until this runs, so without JavaScript the notices
// simply stay on the page.
const main = document.getElementById("main");

function dismiss(button) {
  const notice = button.closest(".notice");
  const container = notice.closest(".messages");
  notice.remove();
  const next = container ? container.querySelector(".notice-dismiss") : null;
  if (container && !container.querySelector(".notice")) container.remove();
  (next || main || document.body).focus({ preventScroll: true });
}

for (const button of document.querySelectorAll(".messages .notice-dismiss")) {
  button.hidden = false;
  button.addEventListener("click", () => dismiss(button));
}
