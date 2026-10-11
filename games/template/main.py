"""Starter game for R36S PyGame Runtime.

Copy this folder to a new game folder and edit this file.
The runtime owns the window, frame loop, scaling, and input collection.
"""
import pygame


class TemplateGame:
    def __init__(self):
        self.font = pygame.font.Font(None, 28)
        self.elapsed = 0.0

    def update(self, dt, inputs):
        self.elapsed += dt
        # Available actions: up, down, left, right, a, b, x, y, start, select.
        # Use inputs.down("left") for held input,
        # inputs.pressed("a") for a one-frame press,
        # inputs.released("a") for a one-frame release.

    def draw(self, surface):
        surface.fill((20, 24, 32))
        title = self.font.render("New Game", True, (240, 240, 240))
        help_text = self.font.render("Edit games/template/main.py to begin", True, (190, 200, 215))
        surface.blit(title, title.get_rect(center=(320, 210)))
        surface.blit(help_text, help_text.get_rect(center=(320, 250)))


def create_game():
    return TemplateGame()
