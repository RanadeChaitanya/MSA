"""Axis-aligned box math in PIXEL space of a canonical page image."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def h(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def intersect(self, o: "Box") -> Optional["Box"]:
        x0, y0 = max(self.x0, o.x0), max(self.y0, o.y0)
        x1, y1 = min(self.x1, o.x1), min(self.y1, o.y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return Box(x0, y0, x1, y1)

    def overlap_ratio(self, o: "Box") -> float:
        """Fraction of SELF covered by o."""
        i = self.intersect(o)
        return 0.0 if i is None or self.area <= 0 else i.area / self.area

    def iou(self, o: "Box") -> float:
        i = self.intersect(o)
        if i is None:
            return 0.0
        u = self.area + o.area - i.area
        return 0.0 if u <= 0 else i.area / u

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def pad(self, p: float) -> "Box":
        return Box(self.x0 - p, self.y0 - p, self.x1 + p, self.y1 + p)

    def clamp(self, W: float, H: float) -> "Box":
        return Box(max(0, self.x0), max(0, self.y0), min(W, self.x1), min(H, self.y1))

    def int_tuple(self) -> tuple[int, int, int, int]:
        return (math.floor(self.x0), math.floor(self.y0), math.ceil(self.x1), math.ceil(self.y1))

    def to_list(self) -> list[float]:
        return [round(self.x0, 1), round(self.y0, 1), round(self.x1, 1), round(self.y1, 1)]

    def dist_to(self, x: float, y: float) -> float:
        dx = max(self.x0 - x, 0, x - self.x1)
        dy = max(self.y0 - y, 0, y - self.y1)
        return math.hypot(dx, dy)

    @staticmethod
    def union_all(boxes: Iterable["Box"]) -> Optional["Box"]:
        boxes = list(boxes)
        if not boxes:
            return None
        return Box(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                   max(b.x1 for b in boxes), max(b.y1 for b in boxes))

    @classmethod
    def from_norm(cls, d: dict, W: float, H: float) -> "Box":
        """Landing AI Gen2 box: {xmin,ymin,xmax,ymax} fractions of the page."""
        return cls(d["xmin"] * W, d["ymin"] * H, d["xmax"] * W, d["ymax"] * H)
