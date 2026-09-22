// 图标资源：应用图标（彩色，48x48）与界面图标（随主题着色，24x24 stroke）
const UI = (d, extra = '') =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${d}${extra}</svg>`

export const ui = {
  search: UI(`<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/>`),
  close: UI(`<path d="M6 6l12 12M18 6L6 18"/>`),
  min: UI(`<path d="M5 12h14"/>`),
  max: UI(`<rect x="5.5" y="5.5" width="13" height="13" rx="1.5"/>`),
  restore: UI(`<rect x="4.5" y="7.5" width="11" height="11" rx="1.5"/><path d="M8 7.5V5.6A1 1 0 019 4.5h9a1 1 0 011 1v9a1 1 0 01-1 1h-1.8"/>`),
  chevLeft: UI(`<path d="M14 6l-6 6 6 6"/>`),
  chevRight: UI(`<path d="M10 6l6 6-6 6"/>`),
  chevDown: UI(`<path d="M6 9l6 6 6-6"/>`),
  chevUp: UI(`<path d="M6 15l6-6 6 6"/>`),
  refresh: UI(`<path d="M20 11a8 8 0 10-2.6 6"/><path d="M20 5v6h-6"/>`),
  wifi: UI(`<path d="M3 9a14 14 0 0118 0"/><path d="M6.5 12.5a9.5 9.5 0 0111 0"/><path d="M10 16a5 5 0 014 0"/><circle cx="12" cy="19" r="1"/>`),
  volume: UI(`<path d="M4 10v4h3l4 3V7L7 10H4z"/><path d="M15.5 9a4 4 0 010 6"/>`),
  battery: UI(`<rect x="2.5" y="8.5" width="16" height="7" rx="1.6"/><path d="M20.5 11v2"/><rect x="4.2" y="10.2" width="10" height="3.6" rx="0.8" fill="currentColor" stroke="none"/>`),
  bell: UI(`<path d="M18 15V10a6 6 0 10-12 0v5l-1.6 2.4h15.2L18 15z"/><path d="M10 20a2.2 2.2 0 004 0"/>`),
  power: UI(`<path d="M12 3v9"/><path d="M6.6 6.6a8 8 0 1010.8 0"/>`),
  plus: UI(`<path d="M12 5v14M5 12h14"/>`),
  trash: UI(`<path d="M4 7h16"/><path d="M9 7V4.8A.8.8 0 019.8 4h4.4a.8.8 0 01.8.8V7"/><path d="M6 7l1 12.2a.8.8 0 00.8.8h8.4a.8.8 0 00.8-.8L18 7"/>`),
  sun: UI(`<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4"/>`),
  moon: UI(`<path d="M20 14.5A8.5 8.5 0 019.5 4 8.5 8.5 0 1020 14.5z"/>`),
  pin: UI(`<path d="M9 4h6l-1 6 3.5 3H6.5L10 10 9 4z"/><path d="M12 13v7"/>`),
  save: UI(`<path d="M5 5h11l3 3v11H5z"/><path d="M8 5v5h7V5"/><rect x="8" y="13" width="8" height="6"/>`),
  copy: UI(`<rect x="8" y="8" width="11" height="11" rx="1.5"/><path d="M16 8V5.5A1.5 1.5 0 0014.5 4H6a1.5 1.5 0 00-1.5 1.5V14"/>`),
  folder: UI(`<path d="M3 6.5A1.5 1.5 0 014.5 5h4l2 2.5h9A1.5 1.5 0 0120 9v9a1.5 1.5 0 01-1.5 1.5h-14A1.5 1.5 0 013 18z"/>`),
  info: UI(`<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.6v.6"/>`),
  grid: UI(`<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/>`),
  list: UI(`<path d="M4 6h2M9 6h11M4 12h2M9 12h11M4 18h2M9 18h11"/>`),
  monitor: UI(`<rect x="3" y="4.5" width="18" height="12" rx="1.5"/><path d="M8.5 20h7M12 16.5V20"/>`),
  palette: UI(`<path d="M12 3a9 9 0 100 18h1.6a2 2 0 001.5-3.3 2 2 0 011.5-3.3H19a3 3 0 003-3A9.7 9.7 0 0012 3z"/><circle cx="8" cy="9.5" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="15.8" cy="10" r="1.2"/><circle cx="7.4" cy="14" r="1.2"/>`),
  gear: UI(`<path d="M10.3 3.5l.6 2.1a6.6 6.6 0 012 .85l2-.8 1.7 1.7-.8 2a6.6 6.6 0 01.85 2l2.1.6v2.4l-2.1.6a6.6 6.6 0 01-.85 2l.8 2-1.7 1.7-2-.8a6.6 6.6 0 01-2 .85l-.6 2.1h-2.4l-.6-2.1a6.6 6.6 0 01-2-.85l-2 .8-1.7-1.7.8-2a6.6 6.6 0 01-.85-2l-2.1-.6v-2.4l2.1-.6a6.6 6.6 0 01.85-2l-.8-2 1.7-1.7 2 .8a6.6 6.6 0 012-.85l.6-2.1z"/><circle cx="10.5" cy="12" r="3"/>`),
  settings: UI(`<path d="M4 6.5h16M4 12h16M4 17.5h16"/><circle cx="9" cy="6.5" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="8" cy="17.5" r="2"/>`),
  edge: UI(`<circle cx="12" cy="12" r="9"/><path d="M4.5 10.5c3.5-4.5 12-4.5 14.5.5-2.5-1.5-6-1-8 1.2-2.6 2.7-1.5 6.3 1 7.3-4-1-6-5.2-7.5-9z"/>`),
  dot: UI(`<circle cx="12" cy="12" r="3.5"/>`),
  user: UI(`<circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0115 0"/>`),
  cast: UI(`<path d="M3 6.5V18a1.5 1.5 0 001.5 1.5H9"/><path d="M3 12.5a7 7 0 017 7M3 16.5a3 3 0 013 3"/><path d="M7 5h13a1.5 1.5 0 011.5 1.5v10A1.5 1.5 0 0120 18h-4"/>`),
  airplane: UI(`<path d="M11 3.5a1 1 0 012 0V9l7.5 4.2v2L13 12.8v4.4l2.8 1.7v1.6L12 19.2l-3.8 1.3v-1.6l2.8-1.7v-4.4L3.5 15.2v-2L11 9z"/>`),
}

export const logo = {
  windows: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <path d="M6 10.5l16-2.3v15.1H6zM24 7.9L42 5.5v17.8H24zM6 25.7h16v15.1L6 38.5zM24 25.7h18V43l-18-2.5z" fill="currentColor"/>
  </svg>`,
  explorer: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <path d="M5 13.5A3.5 3.5 0 018.5 10h9.7l3.6 4.4h17.7A3.5 3.5 0 0143 18v3H5z" fill="#ffb900"/>
    <path d="M5 20h38v15.5A3.5 3.5 0 0139.5 39h-31A3.5 3.5 0 015 35.5z" fill="#ffd04d"/>
    <path d="M5 20h38v4H5z" fill="#f6b53c" opacity=".55"/>
  </svg>`,
  pc: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <rect x="5" y="8" width="38" height="25" rx="2.5" fill="#2b577a"/>
    <rect x="8" y="11" width="32" height="19" rx="1.5" fill="#4cc3ff"/>
    <path d="M18 36h12l1.6 5H16.4z" fill="#8f8f8f"/>
    <rect x="12" y="41" width="24" height="3" rx="1.5" fill="#5c5c5c"/>
  </svg>`,
  notepad: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <rect x="8" y="5" width="32" height="38" rx="2.5" fill="#fff"/>
    <rect x="8" y="5" width="32" height="8" rx="2.5" fill="#0078d4"/>
    <path d="M14 19h20M14 25h20M14 31h14" stroke="#8a8a8a" stroke-width="2" stroke-linecap="round"/>
    <path d="M8 5h4v38H8z" fill="#e5e5e5"/>
  </svg>`,
  calc: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <rect x="8" y="4" width="32" height="40" rx="3.5" fill="#1f3b57"/>
    <rect x="12" y="8" width="24" height="9" rx="1.5" fill="#cfe8ff"/>
    <g fill="#4a90c9">
      <rect x="12" y="21" width="7" height="6" rx="1.5"/>
      <rect x="20.5" y="21" width="7" height="6" rx="1.5"/>
      <rect x="29" y="21" width="7" height="6" rx="1.5"/>
      <rect x="12" y="29.5" width="7" height="6" rx="1.5"/>
      <rect x="20.5" y="29.5" width="7" height="6" rx="1.5"/>
      <rect x="12" y="38" width="15.5" height="4" rx="1.5"/>
    </g>
    <rect x="29" y="29.5" width="7" height="12.5" rx="1.5" fill="#f0a020"/>
  </svg>`,
  settings: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <path d="M24 6l3.2 4.4 5.4-.9 1.3 5.3 5.1 2-1.6 5.2 3.3 4.3-3.3 4.3 1.6 5.2-5.1 2-1.3 5.3-5.4-.9L24 46l-3.2-4.4-5.4.9-1.3-5.3-5.1-2 1.6-5.2L7.3 25.7l3.3-4.3-1.6-5.2 5.1-2 1.3-5.3 5.4.9z" fill="#3b587a"/>
    <circle cx="24" cy="25.5" r="7.5" fill="#cfe8ff"/>
    <circle cx="24" cy="25.5" r="3.4" fill="#3b587a"/>
  </svg>`,
  recycle: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <path d="M10 14h28l-2.6 26a3 3 0 01-3 2.7H15.6a3 3 0 01-3-2.7z" fill="#a8d8b0"/>
    <path d="M10 14h28l-.8 8H10.8z" fill="#7ec48b"/>
    <rect x="7" y="9.5" width="34" height="5" rx="2.5" fill="#4f9d63"/>
    <path d="M19 6h10l1.4 3.5H17.6z" fill="#4f9d63"/>
    <path d="M19 21v16M24 21v16M29 21v16" stroke="#3d7f4f" stroke-width="2" stroke-linecap="round" opacity=".6"/>
  </svg>`,
  recycleFull: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <path d="M10 18h28l-2.6 22a3 3 0 01-3 2.7H15.6a3 3 0 01-3-2.7z" fill="#a8d8b0"/>
    <path d="M6 10.5h36v6.5H6z" fill="#4f9d63"/>
    <path d="M14 5l6 5h-9zM32 4l4 6h-8zM22 3l5 6h-9z" fill="#7ec48b"/>
    <path d="M19 25v13M24 25v13M29 25v13" stroke="#3d7f4f" stroke-width="2" stroke-linecap="round" opacity=".6"/>
  </svg>`,
  terminal: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <rect x="4" y="7" width="40" height="34" rx="3" fill="#0f1721"/>
    <rect x="4" y="7" width="40" height="7" rx="3" fill="#26384a"/>
    <path d="M11 21l6 5-6 5M20 31h11" stroke="#4ade80" stroke-width="2.4" fill="none" stroke-linecap="round"/>
  </svg>`,
  photos: `<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <rect x="5" y="9" width="38" height="30" rx="3" fill="#fff"/>
    <path d="M5 33l11-12 8 9 6-5 13 12v2a3 3 0 01-3 3H8a3 3 0 01-3-3z" fill="#2f9e6f"/>
    <circle cx="16" cy="18" r="4" fill="#f7c948"/>
  </svg>`,
}
