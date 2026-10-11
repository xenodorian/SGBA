"""Visual and input diagnostic for the 640x480 shared runtime."""
import pygame

W, H = 640, 480
BG = (7, 10, 25)
PANEL = (15, 22, 44)
EDGE = (55, 75, 112)
WHITE = (244, 248, 255)
MUTED = (150, 168, 199)
CYAN = (35, 240, 230)
PINK = (255, 48, 151)
YELLOW = (255, 230, 65)
GREEN = (85, 255, 122)
ORANGE = (255, 111, 40)


class InputLab:
    def __init__(self):
        self.font = pygame.font.Font(None, 19)
        self.small = pygame.font.Font(None, 15)
        self.title = pygame.font.Font(None, 29)
        self.raw_font = pygame.font.Font(None, 16)
        self.buttons_down = set()
        self.axes = {}
        self.hat = (0, 0)
        self.last_events = []
        self.seen_controller_event = False

    def handle_event(self, event):
        if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            self.seen_controller_event = True
            idx = int(event.button)
            if event.type == pygame.JOYBUTTONDOWN:
                self.buttons_down.add(idx)
                self._log("BUTTON %02d DOWN" % idx)
            else:
                self.buttons_down.discard(idx)
                self._log("BUTTON %02d UP" % idx)
        elif event.type == pygame.JOYAXISMOTION:
            self.seen_controller_event = True
            idx = int(event.axis)
            self.axes[idx] = float(event.value)
            self._log("AXIS %02d  %+0.2f" % (idx, event.value))
        elif event.type == pygame.JOYHATMOTION:
            self.seen_controller_event = True
            self.hat = tuple(event.value)
            self._log("HAT %s" % (self.hat,))
        elif event.type == pygame.JOYDEVICEREMOVED:
            self.buttons_down.clear()
            self.axes.clear()
            self.hat = (0, 0)
            self._log("DEVICE REMOVED")
        elif event.type == pygame.JOYDEVICEADDED:
            self._log("DEVICE ADDED")

    def _log(self, message):
        self.last_events.insert(0, message)
        del self.last_events[5:]

    def update(self, dt, inputs):
        pass

    def _text(self, surface, text, pos, color=WHITE, font=None):
        surface.blit((font or self.font).render(str(text), True, color), pos)

    def _center(self, surface, text, center, color=WHITE, font=None):
        image = (font or self.font).render(str(text), True, color)
        surface.blit(image, image.get_rect(center=center))

    def _dither(self, surface, rect, color_a, color_b):
        x0, y0, width, height = rect
        # Draw native-resolution 2x2 checker pixels without smoothing.
        for x in range(x0, x0 + width, 2):
            t = (x - x0) / max(1, width - 1)
            a = tuple(round(color_a[i] * (1 - t) + color_b[i] * t) for i in range(3))
            b = tuple(round((color_a[i] * 0.15 + color_b[i] * 0.85) * (1 - t) +
                            (color_b[i] * 0.15 + color_a[i] * 0.85) * t) for i in range(3))
            for y in range(y0, y0 + height, 2):
                c = a if ((x // 2 + y // 2) & 1) == 0 else b
                pygame.draw.rect(surface, c, (x, y, 2, 2))

    def _panel(self, surface, rect):
        pygame.draw.rect(surface, PANEL, rect)
        pygame.draw.rect(surface, EDGE, rect, 1)

    def _lit(self, surface, rect, active, accent=CYAN):
        pygame.draw.rect(surface, accent if active else (26, 34, 58), rect)
        pygame.draw.rect(surface, WHITE if active else EDGE, rect, 1)

    def draw(self, surface):
        surface.fill(BG)
        self._text(surface, "PIXEL / INPUT LAB", (16, 10), WHITE, self.title)
        self._text(surface, "640 x 480 LOGICAL FRAME  |  SHARP PIXELS  |  RAW CONTROLLER EVENTS",
                   (17, 36), MUTED, self.small)

        self._dither(surface, (16, 58, 298, 91), (255, 28, 94), (255, 208, 25))
        self._dither(surface, (326, 58, 298, 91), (0, 230, 255), (73, 48, 255))
        pygame.draw.rect(surface, WHITE, (16, 58, 298, 91), 1)
        pygame.draw.rect(surface, WHITE, (326, 58, 298, 91), 1)
        self._text(surface, "A / MAGENTA -> AMBER", (22, 62), WHITE, self.small)
        self._text(surface, "B / CYAN -> BLUE", (332, 62), WHITE, self.small)
        self._text(surface, "2x2 PIXEL DITHER", (22, 126), WHITE, self.small)
        self._text(surface, "2x2 PIXEL DITHER", (332, 126), WHITE, self.small)

        self._panel(surface, (16, 158, 298, 188))
        self._panel(surface, (326, 158, 298, 188))
        self._text(surface, "D-PAD / FACE BUTTONS", (25, 165), CYAN, self.font)
        self._text(surface, "SHOULDERS / STICKS", (335, 165), PINK, self.font)

        dpad = {"UP": (72, 202, 25, 20, "up"), "LEFT": (45, 224, 25, 20, "left"),
                "RIGHT": (99, 224, 25, 20, "right"), "DOWN": (72, 246, 25, 20, "down")}
        for label, (x, y, w, h, action) in dpad.items():
            active = inputs.down(action)
            self._lit(surface, (x, y, w, h), active, CYAN)
            self._center(surface, label[0], (x + w // 2, y + h // 2),
                         BG if active else WHITE, self.small)

        face = [("Y", 220, 205, "y", YELLOW), ("X", 194, 229, "x", PINK),
                ("B", 246, 229, "b", ORANGE), ("A", 220, 253, "a", GREEN)]
        for label, x, y, action, color in face:
            active = inputs.down(action)
            pygame.draw.circle(surface, color if active else (36, 43, 65), (x, y), 11)
            pygame.draw.circle(surface, WHITE if active else EDGE, (x, y), 11, 1)
            self._center(surface, label, (x, y), BG if active else color, self.small)

        for i, (label, action) in enumerate((("SELECT", "select"), ("START", "start"), ("GUIDE", "guide"))):
            x = 25 + i * 88
            active = inputs.down(action)
            self._lit(surface, (x, 286, 78, 22), active, PINK)
            self._center(surface, label, (x + 39, 297), WHITE, self.small)

        for label, action, x in (("L1", "l1", 341), ("R1", "r1", 485),
                                  ("L2", "l2", 341), ("R2", "r2", 485)):
            y = 195 if label in ("L1", "R1") else 224
            active = inputs.down(action)
            self._lit(surface, (x, y, 118, 23), active, PINK if label.endswith("1") else ORANGE)
            self._center(surface, label + (" ACTIVE" if active else ""), (x + 59, y + 12),
                         WHITE, self.small)

        for cx, cy, title, actions, axes in (
            (385, 283, "L3 / LEFT STICK", ("l3", "left", "right", "up", "down"), (0, 1)),
            (535, 283, "R3 / RIGHT STICK", ("r3", "rs_left", "rs_right", "rs_up", "rs_down"), (2, 3)),
        ):
            active = any(inputs.down(a) for a in actions)
            pygame.draw.circle(surface, (37, 49, 77), (cx, cy), 24)
            pygame.draw.circle(surface, CYAN if active else EDGE, (cx, cy), 24, 2)
            dx = int(self.axes.get(axes[0], 0.0) * 12)
            dy = int(self.axes.get(axes[1], 0.0) * 12)
            pygame.draw.circle(surface, PINK if active else MUTED, (cx + dx, cy + dy), 8)
            self._center(surface, title, (cx, 318), WHITE, self.small)
        self._text(surface, "L2/R2 triggers may report as AXIS 4/5", (337, 329), MUTED, self.small)

        self._panel(surface, (16, 356, 608, 108))
        self._text(surface, "RAW SDL INPUT MONITOR", (25, 363), YELLOW, self.font)
        status = "CONTROLLER EVENT SEEN" if self.seen_controller_event else "MOVE / PRESS ANY CONTROLLER INPUT"
        self._text(surface, status, (265, 365), GREEN if self.seen_controller_event else MUTED, self.small)
        for i in range(16):
            x = 26 + i * 35
            active = i in self.buttons_down
            self._lit(surface, (x, 386, 29, 22), active, ORANGE)
            self._center(surface, str(i), (x + 14, 397), BG if active else WHITE, self.small)
        axis_parts = ["A%d:%+.1f" % (i, self.axes.get(i, 0.0)) for i in range(6)]
        self._text(surface, " ".join(axis_parts), (26, 414), CYAN, self.raw_font)
        self._text(surface, "HAT: %s" % (self.hat,), (326, 414), CYAN, self.raw_font)
        self._text(surface, "LAST EVENTS:", (26, 438), MUTED, self.small)
        for i, event_text in enumerate(self.last_events[:3]):
            self._text(surface, event_text, (112 + i * 165, 438), WHITE, self.raw_font)
        self._text(surface, "KEYBOARD: arrows/WASD move | Z/Space=A | X/Ctrl=B | Q=X | E=Y",
                   (16, 468), MUTED, self.small)


def create_game():
    return InputLab()
