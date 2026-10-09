# Playwrights egen avbild: Node, Chromium och de typsnitt Chromium behöver,
# i samma version som paketet i package.json. Utan rätt version hittar
# Playwright inte webbläsaren.
FROM mcr.microsoft.com/playwright:v1.56.1-noble

# ffmpeg sätter ihop bildrutorna och ljudet. Liberation Mono är filmens
# typsnitt; det följer med i avbilden men installeras för säkerhets skull.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json ./
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --omit=dev --no-audit --no-fund
COPY film.html ljud.mjs rendera.mjs kontroll.mjs server.mjs ./

ENV NODE_ENV=production
CMD ["node", "server.mjs"]
