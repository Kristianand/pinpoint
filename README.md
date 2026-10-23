# Pinpoint

A GeoGuessr-style party game for 2–10 players. Everyone plays on their own phone: iPhone, Android or a laptop. On iPhone you add it to the home screen, and from then on it opens full-screen with its own icon, like an app.

## Put it online (once)

To play with friends wherever they are, the game needs to run on a public server. Render's free plan works for this:

1. Put this folder in a GitHub repository. `config.json` is excluded, so a Google key never ends up there.
2. On [render.com](https://render.com), choose **New → Blueprint** and pick the repository. Render reads `render.yaml` and starts the game.
3. You get an address like `https://pinpoint-xxxx.onrender.com`. That's the app.

On the free plan the server sleeps after 15 idle minutes. The first person to open the app may wait about a minute while it wakes up.

## Install it on an iPhone

1. Open the address in **Safari**.
2. Tap **Share** (the square with an arrow), then **Add to Home Screen**, then **Add**.
3. Open **Pinpoint** from the home screen.

If you close the app during a match, opening it again puts you back in your game.

## Play

1. One person types their name and taps **Create a new game**. They get a 4-letter code, like `KBRT`, and become the host.
2. The host taps **Invite friends** to send the link by Messages or WhatsApp. Friends can also open Pinpoint and type the code.
3. The host picks the number of rounds, the time limit and whether moving is allowed, then taps **Start match**.
4. Each round, look at the place, tap **Open map**, drop a pin and tap **Guess**. The round ends when everyone has guessed or time runs out. A guess at the exact spot scores 5,000 points. Score drops with distance, so a guess 1,500 km off scores about 1,800.

Several groups can play on the same server at once, each with its own code.

## Imagery: photos or real Street View

- **Without a key (default):** each round shows 3–5 photos people took near a random place, from Wikimedia Commons. The credits appear after each round.
- **With a Google Maps key:** real, walkable Google Street View, like GeoGuessr.
  1. In Google Cloud Console, create a project and an API key.
  2. Turn on **Maps JavaScript API** and **Street View Static API** for it.
  3. Under the key's restrictions, allow only your game's address (for example `https://pinpoint-xxxx.onrender.com/*`), so nobody else can use it.
  4. On Render, set the `GOOGLE_MAPS_API_KEY` environment variable to the key. When running on your own computer, copy `config.example.json` to `config.json` and paste the key in instead.

  Google gives a monthly free credit, which covers casual games with friends.

## Run it on your own computer instead

Double-click `start.bat`, or run `python server.py`. Friends on the same Wi-Fi open the address it prints, for example `http://192.168.1.20:8000`. The first time it runs, allow Python on **private networks** when Windows asks. Over plain `http`, iPhones can still add it to the home screen, but it works only while your computer is on and everyone is on the same network.

## Customise

- `locations.py` lists about 190 starting places. Add or remove lines to change where rounds can land.
- `python make_icons.py` redraws the app icons in `static/`.

## Files

- `server.py`: game server and rules, using only the Python standard library
- `locations.py`: starting places
- `static/`: the app (`index.html`, `app.js`, `style.css`, icons, `manifest.webmanifest`)
- `render.yaml`: hosting setup for Render
