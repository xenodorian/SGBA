"""Compact starter game. Runtime owns the loop, display and scaling."""
import pygame


class TemplateGame:
    def __init__(self):
        self.font = pygame.font.Font(None, 14)
        self.player_x = 120
        self.player_y = 120

    def update(self, dt, inputs):
        speed = 48
        if inputs.down("left"):
            self.player_x -= speed * dt
        if inputs.down("right"):
            self.player_x += speed * dt
        if inputs.down("up"):
            self.player_y -= speed * dt
        if inputs.down("down"):
            self.player_y += speed * dt
        self.player_x = max(4, min(236, self.player_x))
        self.player_y = max(4, min(156, self.player_y))

    def draw(self, surface):
        surface.fill((20, 24, 32))
        pygame.draw.rect(surface, (80, 120, 160), (8, 8, 224, 144), 1)
        pygame.draw.rect(surface, (230, 210, 100),
                         (round(self.player_x) - 3, round(self.player_y) - 3, 7, 7))
        surface.blit(self.font.render("SGBA STARTER", True, (240, 240, 240)), (12, 14))
        surface.blit(self.font.render("D-pad to move", True, (190, 200, 215)), (12, 28))


def create_game():
    return TemplateGame()
