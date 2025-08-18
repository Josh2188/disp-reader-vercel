// Service Worker for PWA

const CACHE_NAME = 'ptt-mobile-cache-v1';
const urlsToCache = [
  '/',
  '/index.html', // 根據您的檔名調整
  'https://cdn.tailwindcss.com',
  'https://unpkg.com/pinch-zoom-js@2.3.4/dist/pinch-zoom.umd.js',
  'https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap'
];

// 安裝 Service Worker
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
  );
});

// 攔截網路請求
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // 如果快取中有，就直接回傳
        if (response) {
          return response;
        }

        // 否則，發出網路請求
        return fetch(event.request).then(
          response => {
            // 如果請求失敗，或不是我們要快取的資源，就直接回傳
            if(!response || response.status !== 200 || response.type !== 'basic') {
              return response;
            }

            // 複製一份請求的回應
            const responseToCache = response.clone();

            caches.open(CACHE_NAME)
              .then(cache => {
                cache.put(event.request, responseToCache);
              });

            return response;
          }
        );
      })
    );
});

// 啟用 Service Worker 並清除舊快取
self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});
