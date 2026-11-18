const field = document.querySelector("[data-primary-field]");
if (field) {
  field.focus();
}

const composer = document.querySelector(".composer textarea");
if (composer) {
  composer.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      composer.form.requestSubmit();
    }
  });
}
