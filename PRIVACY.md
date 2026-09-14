# Privacy Policy — Tododisplay

_Last updated: 2026-09-14_

Tododisplay ("the app") is a personal, self-hosted information display that
runs on the owner's own Raspberry Pi. This policy explains what data the app
accesses and how it is handled.

## Who this applies to

Tododisplay is a personal project operated by a single individual for their own
use. It is not a commercial service and is not intended for use by the general
public.

## What data the app accesses

With the user's explicit consent via Google OAuth, the app may read the
following data from the user's own Google Health account:

- Activity and fitness data (for example, daily steps, resting heart rate, and
  active minutes).
- Sleep data (for example, sleep sessions, bedtime, wake time, and sleep
  stages).

The app requests **read-only** access to this data.

## How the data is used

- Data is retrieved from the Google Health API and displayed on the owner's own
  private dashboard (web pages and a physical display) for personal review.
- Sleep session times may be recorded locally to summarize sleep consistency.

## How the data is stored and shared

- All data stays on the owner's own device (a Raspberry Pi) and is accessed only
  over the owner's private network.
- The app does **not** sell, share, or transmit health data to any third party.
- The app does not use health data for advertising or any purpose other than
  displaying it to the owner.

## Data retention and deletion

- Cached data is transient and refreshed from the source. Locally stored
  summaries can be deleted by the owner at any time by removing the relevant
  files on the device.
- Access can be revoked at any time from the Google Account permissions page
  (https://myaccount.google.com/permissions), which stops all further data
  access.

## Contact

This is a personal project. Questions can be directed to the repository owner
via the project's GitHub page.
