#!/usr/bin/env python3
"""
display.py - Lightweight fullscreen list display for low-RAM Raspberry Pi boards
(e.g. Pi Zero 2 W). Draws the todo and shopping lists directly to the screen with
pygame, reading from the same SQLite database the Flask web app uses.

No browser or desktop environment required. Add/remove items from any device via
the Flask web interface at http://<pi-ip>:5000 -- changes appear here automatically.
"""
import os
import sqlite3
import sys
import time

import pygame

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lists.db")

# --- Appearance ---
BG_COLOR = (26, 26, 46)        # dark navy
PANEL_COLOR = (22, 33, 62)     # slightly lighter panel
HEADER_COLOR = (0, 212, 255)   # cyan
TEXT_COLOR = (234, 234, 234)   # off-white
DONE_COLOR = (120, 120, 130)   # grey for completed
CLOCK_COLOR = (110, 110, 120)

REFRESH_SECONDS = 5            # how often to re-read the database


def read_items(list_type):
    """Read items for a list from the database. Returns [] if DB not ready."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT text, done FROM items WHERE list_type = ? "
            "ORDER BY done ASC, created_at DESC",
            (list_type,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def draw_panel(screen, fonts, rect, title, items):
    """Draw one list panel (title + items) inside the given rect."""
    x, y, w, h = rect
    panel_rect = pygame.Rect(x, y, w, h)
    pygame.draw.rect(screen, PANEL_COLOR, panel_rect, border_radius=16)

    pad = 24
    # Title
    title_surf = fonts["title"].render(title, True, HEADER_COLOR)
    title_x = x + (w - title_surf.get_width()) // 2
    screen.blit(title_surf, (title_x, y + pad))

    # Items
    line_y = y + pad + title_surf.get_height() + 20
    line_height = fonts["item"].get_height() + 16
    for item in items:
        if line_y + line_height > y + h - pad:
            break  # ran out of vertical space in this panel
        color = DONE_COLOR if item["done"] else TEXT_COLOR
        prefix = "\u2713 " if item["done"] else "\u2022 "
        text = prefix + item["text"]
        # Truncate overly long lines to fit the panel width
        max_w = w - 2 * pad
        rendered = fonts["item"].render(text, True, color)
        if rendered.get_width() > max_w:
            while rendered.get_width() > max_w and len(text) > 4:
                text = text[:-2]
                rendered = fonts["item"].render(text + "\u2026", True, color)
        screen.blit(rendered, (x + pad, line_y))
        line_y += line_height

    if not items:
        empty_surf = fonts["item"].render("No items yet", True, DONE_COLOR)
        screen.blit(empty_surf, (x + pad, line_y))


def main():
    pygame.init()
    pygame.mouse.set_visible(False)

    # Fullscreen at the display's native resolution
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("List Display")
    sw, sh = screen.get_size()

    # Scale fonts to the screen height so it's readable on any monitor size.
    # Use pygame's bundled font (pygame.font.Font(None, ...)) instead of a system
    # font: it needs no fontconfig/fc-list lookup, which is slow and can time out
    # on low-power boards like the Pi Zero 2 W.
    def make_font(size, bold=False):
        f = pygame.font.Font(None, size)
        f.set_bold(bold)
        return f

    fonts = {
        "title": make_font(max(34, sh // 15), bold=True),
        "item": make_font(max(24, sh // 22)),
        "clock": make_font(max(18, sh // 30)),
    }

    clock = pygame.time.Clock()
    last_refresh = 0.0
    todo_items = []
    shopping_items = []

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                # Press Esc or Q to quit
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False

        # Refresh data periodically
        now = time.time()
        if now - last_refresh >= REFRESH_SECONDS:
            todo_items = read_items("todo")
            shopping_items = read_items("shopping")
            last_refresh = now

        # --- Draw ---
        screen.fill(BG_COLOR)

        gap = 24
        margin = 24
        clock_h = fonts["clock"].get_height() + 20
        # Side-by-side columns: each panel is narrow but uses the FULL screen
        # height, so many more items fit down the length of each list.
        panel_w = (sw - 2 * margin - gap) // 2
        panel_h = sh - 2 * margin - clock_h

        draw_panel(screen, fonts,
                   (margin, margin, panel_w, panel_h),
                   "Todo List", todo_items)
        draw_panel(screen, fonts,
                   (margin + panel_w + gap, margin, panel_w, panel_h),
                   "Shopping List", shopping_items)

        # Clock / date at the bottom
        stamp = time.strftime("%A, %B %d  \u2022  %I:%M %p")
        clock_surf = fonts["clock"].render(stamp, True, CLOCK_COLOR)
        clock_x = (sw - clock_surf.get_width()) // 2
        screen.blit(clock_surf, (clock_x, sh - clock_h + 4))

        pygame.display.flip()
        clock.tick(10)  # 10 FPS is plenty; keeps CPU/RAM usage low

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
