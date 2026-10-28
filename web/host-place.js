// Where the Hermes plugin has the live player (updated from postMessage in app.js).

let hostPlace = "full player page";

export function setHostPlayerPlace(place) {
  if (typeof place === "string" && place) hostPlace = place;
}

export function getHostPlayerPlace() {
  return hostPlace;
}

const LABELS = {
  docked: "Frame",
  floating: "Pop into Hermes",
  mini: "Mini player",
  desktop: "Desktop window",
  page: "Full screen",
};

export function labelHostPlace(id) {
  return LABELS[id] || id || hostPlace;
}
