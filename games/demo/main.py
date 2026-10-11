"""Small runtime smoke-test game."""
import pygame


class DemoGame:
    def __init__(self):
        self.x = 320
        self.y = 240
        self.font = pygame.font.Font(None, 30)

    def update(self, dt, inputs):
        speed = 220 * dt
        if inputs.down("left"):
            self.x -= speed
        if inputs.down("right"):
            self.x += speed
        if inputs.down("up"):
            self.y -= speed
        if inputs.down("down"):
            self.y += speed
        self.x = max(16, min(624, self.x))
        self.y = max(16, min(464, self.y))

    def draw(self, surface):
        surface.fill((18, 24, 38))
        pygame.draw.rect(surface, (46, 58, 80), (24, 24, 592, 432), 2)
        pygame.draw.circle(surface, (90, 210, 160), (round(self.x), round(self.y)), 16)
        title = self.font.render("R36S PyGame Runtime", True, (245, 245, 245))
        hint = self.font.render("Move with arrows / WASD or a gamepad", True, (200, 210, 225))
        surface.blit(title, title.get_rect(center=(320, 72)))
        surface.blit(hint, hint.get_rect(center=(320, 110)))


def create_game():
    return DemoGame()
