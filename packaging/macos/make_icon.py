"""Generate the Consensus app icon as a 1024x1024 PNG.

Design: two overlapping speech bubbles (the discussion) converging on a
shared centre dot (the consensus), on an indigo gradient squircle with the
standard macOS Big Sur margin.
"""

from PIL import Image, ImageDraw

SIZE = 1024
# macOS icons leave ~10% transparent margin around the squircle
MARGIN = 100
RADIUS = 185

TOP_COLOR = (74, 95, 193)      # indigo
BOTTOM_COLOR = (36, 45, 99)    # deep indigo
BUBBLE_A = (255, 255, 255, 235)
BUBBLE_B = (159, 180, 255, 210)
CENTER_DOT = (36, 45, 99, 255)


def gradient_squircle() -> Image.Image:
    """Vertical gradient clipped to a rounded rectangle with margin."""
    grad = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(grad)
    span = SIZE - 2 * MARGIN
    for y in range(MARGIN, SIZE - MARGIN):
        t = (y - MARGIN) / span
        color = tuple(
            round(TOP_COLOR[i] + (BOTTOM_COLOR[i] - TOP_COLOR[i]) * t)
            for i in range(3)
        )
        draw.line([(MARGIN, y), (SIZE - MARGIN, y)], fill=color + (255,))

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [MARGIN, MARGIN, SIZE - MARGIN, SIZE - MARGIN],
        radius=RADIUS, fill=255,
    )
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def bubble(draw: ImageDraw.ImageDraw, box: tuple, tail: list, color: tuple) -> None:
    """A rounded speech bubble with a triangular tail."""
    draw.rounded_rectangle(box, radius=90, fill=color)
    draw.polygon(tail, fill=color)


def main() -> None:
    img = gradient_squircle()
    overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Left bubble, tail pointing down-left
    bubble(
        draw,
        (195, 300, 590, 580),
        [(280, 555), (235, 675), (400, 580)],
        BUBBLE_B,
    )
    # Right bubble, tail pointing down-right, overlapping the first
    bubble(
        draw,
        (430, 400, 825, 680),
        [(740, 655), (785, 775), (620, 680)],
        BUBBLE_A,
    )
    # Consensus dot in the overlap zone
    draw.ellipse((462, 452, 558, 548), fill=CENTER_DOT)

    img = Image.alpha_composite(img, overlay)
    img.save("icon_1024.png")
    print("Wrote icon_1024.png")


if __name__ == "__main__":
    main()
