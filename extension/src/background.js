// Service worker: the entry points that put a link in front of the launcher.
//
// A context-menu click cannot open the toolbar popup (there is no API for it),
// so both entry points open the same small window instead. That also means the
// launcher survives losing focus, which a popup would not — picking a browser
// and toggling advanced options takes longer than a popup usually lives.

const api = globalThis.browser ?? globalThis.chrome;

const MENU_LINK = 'cove-open-link';
const MENU_PAGE = 'cove-open-page';

api.runtime.onInstalled.addListener(() => {
  api.contextMenus.removeAll(() => {
    api.contextMenus.create({
      id: MENU_LINK,
      title: 'Open link in Cove workspace',
      contexts: ['link'],
    });
    api.contextMenus.create({
      id: MENU_PAGE,
      title: 'Open this page in Cove workspace',
      contexts: ['page'],
    });
  });
});

api.contextMenus.onClicked.addListener((info, tab) => {
  const url = info.menuItemId === MENU_LINK ? info.linkUrl : info.pageUrl || (tab && tab.url);
  if (url) openLauncher(url);
});

api.action.onClicked.addListener((tab) => {
  openLauncher(tab && tab.url);
});

function openLauncher(url) {
  // Only http(s) can be handed to a Cove browser workspace; the API rejects
  // anything else, so catch it here rather than after a round trip.
  const target = /^https?:\/\//i.test(url || '') ? url : '';
  const page = api.runtime.getURL(`src/launch.html${target ? `?url=${encodeURIComponent(target)}` : ''}`);
  api.windows.create({ url: page, type: 'popup', width: 460, height: 680 });
}
