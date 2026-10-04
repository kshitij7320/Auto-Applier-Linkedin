import math
import random
import asyncio
import sys
from typing import List, Tuple, Optional, Union
from playwright.async_api import Page, Locator, Playwright, Browser, async_playwright

def _cubic_bezier(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    return (1 - t)**3 * p0 + 3 * (1 - t)**2 * t * p1 + 3 * (1 - t) * t**2 * p2 + t**3 * p3

def generate_bezier_trajectory(
    start: Tuple[float, float],
    end: Tuple[float, float],
    steps: int = 25
) -> List[Tuple[float, float]]:
    x0, y0 = start
    x3, y3 = end
    dx = x3 - x0
    dy = y3 - y0
    dist = math.hypot(dx, dy)

    if dist < 1.0 or steps <= 1:
        return [start, end] if steps > 1 else [end]

    # Deviation based on distance
    deviation = min(max(dist * 0.25, 20.0), 120.0)
    angle = math.atan2(dy, dx)
    perp_angle = angle + (math.pi / 2 if random.random() < 0.5 else -math.pi / 2)

    offset1 = random.uniform(0.1, 0.4)
    offset2 = random.uniform(0.6, 0.9)
    dev1 = random.uniform(-deviation, deviation)
    dev2 = random.uniform(-deviation, deviation)

    x1 = x0 + dx * offset1 + math.cos(perp_angle) * dev1
    y1 = y0 + dy * offset1 + math.sin(perp_angle) * dev1
    x2 = x0 + dx * offset2 + math.cos(perp_angle) * dev2
    y2 = y0 + dy * offset2 + math.sin(perp_angle) * dev2

    trajectory = []
    for i in range(steps):
        t = i / (steps - 1)
        # Ease-in, ease-out using cubic parameterization
        t_eased = t * t * (3 - 2 * t)
        xt = _cubic_bezier(x0, x1, x2, x3, t_eased)
        yt = _cubic_bezier(y0, y1, y2, y3, t_eased)
        trajectory.append((xt, yt))
    return trajectory

def generate_overshoot_trajectory(
    start: Tuple[float, float],
    end: Tuple[float, float]
) -> List[Tuple[float, float]]:
    x0, y0 = start
    x_target, y_target = end
    dx = x_target - x0
    dy = y_target - y0
    dist = math.hypot(dx, dy)

    if dist < 15.0:
        return generate_bezier_trajectory(start, end, steps=10)

    # Overshoot destination 4-12px beyond the target along movement vector with slight deviation
    angle = math.atan2(dy, dx)
    overshoot_dist = random.uniform(4.0, 12.0)
    overshoot_angle = angle + random.uniform(-0.15, 0.15)
    x_over = x_target + math.cos(overshoot_angle) * overshoot_dist
    y_over = y_target + math.sin(overshoot_angle) * overshoot_dist

    primary_steps = random.randint(22, 32)
    corrective_steps = random.randint(8, 14)

    primary_path = generate_bezier_trajectory((x0, y0), (x_over, y_over), steps=primary_steps)
    corrective_path = generate_bezier_trajectory((x_over, y_over), (x_target, y_target), steps=corrective_steps)

    # Combine without duplicating the junction point
    return primary_path + corrective_path[1:]

class StealthController:
    def __init__(self, page: Page):
        self.page = page
        self.current_x = random.uniform(100, 300)
        self.current_y = random.uniform(100, 300)

    async def move_to(self, target_x: float, target_y: float) -> None:
        path = generate_overshoot_trajectory((self.current_x, self.current_y), (target_x, target_y))
        for x, y in path:
            await self.page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.015))
        self.current_x = target_x
        self.current_y = target_y

    async def click_element(self, locator_or_selector: Union[str, Locator]) -> None:
        locator = locator_or_selector if isinstance(locator_or_selector, Locator) else self.page.locator(locator_or_selector)
        await locator.scroll_into_view_if_needed()
        box = await locator.bounding_box()
        if not box:
            raise RuntimeError(f"Element bounding box is null or element is hidden: {locator_or_selector}")

        # Pick random coordinate within padding boundaries
        pad_x = max(2.0, min(box["width"] * 0.2, 10.0))
        pad_y = max(2.0, min(box["height"] * 0.2, 10.0))
        target_x = box["x"] + random.uniform(pad_x, max(box["width"] - pad_x, pad_x))
        target_y = box["y"] + random.uniform(pad_y, max(box["height"] - pad_y, pad_y))

        await self.move_to(target_x, target_y)
        await asyncio.sleep(random.uniform(0.05, 0.12))
        await self.page.mouse.down()
        await asyncio.sleep(random.uniform(0.04, 0.09))
        await self.page.mouse.up()
        await asyncio.sleep(random.uniform(0.08, 0.2))

    async def type_human(self, text: str, locator_or_selector: Optional[Union[str, Locator]] = None) -> None:
        if locator_or_selector is not None:
            await self.click_element(locator_or_selector)
            # Clear existing content safely with native select-all and backspace
            modifier = "Meta+A" if sys.platform == "darwin" else "Control+A"
            await self.page.keyboard.press(modifier)
            await asyncio.sleep(0.05)
            await self.page.keyboard.press("Backspace")
            await asyncio.sleep(0.05)

        for char in text:
            await self.page.keyboard.press(char)
            # Sample typing latency with log-normal distribution (~60ms median)
            base_delay = random.lognormvariate(-2.8, 0.35)
            # Natural micro-pauses after space, comma, period, or uppercase
            if char in " .,?!":
                base_delay += random.uniform(0.08, 0.20)
            elif char.isupper() or char.isdigit():
                base_delay += random.uniform(0.04, 0.10)
            await asyncio.sleep(min(max(base_delay, 0.02), 0.35))

    async def random_delay(self, min_s: float = 1.0, max_s: float = 2.5) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

async def connect_to_cdp(endpoint_url: str = "http://localhost:9222") -> Tuple[Playwright, Browser, Page]:
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(endpoint_url)
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("No active browser context found via CDP.")
    context = contexts[0]
    pages = context.pages
    page = pages[0] if pages else await context.new_page()
    return pw, browser, page
