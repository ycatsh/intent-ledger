const timezoneField = document.querySelector("[data-detect-timezone]");
const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
if (timezoneField && localZone) timezoneField.value = localZone;
