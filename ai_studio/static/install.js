'use strict';
// Nexus share / installation controls: never include access codes,
// provider keys, research history, or query parameters in shared links.
(() => {
  const PUBLIC_PREVIEW_PATH = '/demo';
  const shareUrl = new URL(PUBLIC_PREVIEW_PATH, window.location.origin).href;
  const installButton = document.getElementById('install-nexus');
  const shareButton = document.getElementById('share-nexus');
  const status = document.getElementById('share-status');
  let pendingInstall = null;

  const setStatus = text => {
    if (status) status.textContent = text;
  };

  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
        .catch(() => setStatus('You can still open Nexus in your browser if installation is unavailable.'));
    }, { once: true });
  }

  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    pendingInstall = event;
    if (installButton) installButton.textContent = 'Install Nexus app ↓';
  });

  window.addEventListener('appinstalled', () => {
    pendingInstall = null;
    setStatus('Nexus was added to this device. Your private workspace still requires its access code.');
  });

  async function sharePublicPreview() {
    if (navigator.share) {
      try {
        await navigator.share({
          title: 'Nexus AI Studio — Public Preview',
          text: 'Explore a safe, interactive preview of Nexus AI Studio.',
          url: shareUrl
        });
        setStatus('Public preview shared. No private workspace data was included.');
        return;
      } catch (error) {
        if (error && error.name === 'AbortError') return;
      }
    }
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(shareUrl);
      setStatus('Public preview link copied. Share it freely: ' + shareUrl);
    } catch (_) {
      setStatus('Copy this public preview URL: ' + shareUrl);
    }
  }

  async function installNexus() {
    if (window.matchMedia('(display-mode: standalone)').matches) {
      setStatus('Nexus is already running as an installed web app.');
      return;
    }
    if (pendingInstall) {
      try {
        await pendingInstall.prompt();
        await pendingInstall.userChoice;
        pendingInstall = null;
        setStatus('If you accepted, Nexus will appear on your home screen.');
      } catch (_) {
        setStatus('In Android Chrome, open the ⋮ menu and choose Install app or Add to Home screen.');
      }
      return;
    }
    setStatus('Android Chrome: tap ⋮ → Add to Home screen / Install app. iPhone Safari: Share → Add to Home Screen. Installation is free.');
  }

  if (shareButton) shareButton.addEventListener('click', sharePublicPreview);
  if (installButton) installButton.addEventListener('click', installNexus);
})();
