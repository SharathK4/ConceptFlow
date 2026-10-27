from manim import *
from cfkit import *
import numpy as np

M = [[2, 1], [0, 1.5]]


def grids():
    """A faint copy of the plane that stays put, and the grid that moves."""
    ghost = NumberPlane(
        x_range=(-9, 9, 1),
        y_range=(-6, 6, 1),
        background_line_style={"stroke_color": FAINT, "stroke_width": 1, "stroke_opacity": 0.55},
        axis_config={"stroke_color": FAINT, "stroke_width": 1.5},
    )
    plane = NumberPlane(
        x_range=(-9, 9, 1),
        y_range=(-6, 6, 1),
        background_line_style={"stroke_color": PRIMARY, "stroke_width": 1.6, "stroke_opacity": 0.6},
        axis_config={"stroke_color": INK, "stroke_width": 2.2},
    )
    return ghost, plane


def arrow_to(x, y, color):
    return Vector([x, y], color=color, stroke_width=6, max_tip_length_to_length_ratio=0.22)


def basis():
    return arrow_to(1, 0, POSITIVE), arrow_to(0, 1, NEGATIVE)


def unit_square():
    return Polygon(ORIGIN, RIGHT, RIGHT + UP, UP, color=HIGHLIGHT, fill_opacity=0.32, stroke_width=3)


def matrix_card(rows):
    matrix = Matrix(rows, h_buff=1.1).scale(0.82)
    matrix.get_columns()[0].set_color(POSITIVE)
    matrix.get_columns()[1].set_color(NEGATIVE)
    matrix.to_corner(UR, buff=0.6)
    return VGroup(plate(matrix), matrix)


class S1(ConceptScene):
    def construct(self):
        with self.beat("s1b1") as b:
            self.title_card("The Determinant", "how much a matrix stretches space", run_time=b.t(0.7))

        with self.beat("s1b2") as b:
            self.clear_stage(run_time=0.5)
            ghost, plane = grids()
            i_hat, j_hat = basis()
            self.play(FadeIn(ghost), Create(plane, lag_ratio=0.04), run_time=b.t(0.45))
            self.play(GrowArrow(i_hat), GrowArrow(j_hat), run_time=b.t(0.25))

        with self.beat("s1b3") as b:
            card = matrix_card(M)
            self.play(FadeIn(card, shift=0.15 * DOWN), run_time=b.t(0.2))
            self.play(
                ApplyMatrix(M, plane),
                Transform(i_hat, arrow_to(2, 0, POSITIVE)),
                Transform(j_hat, arrow_to(1, 1.5, NEGATIVE)),
                run_time=b.t(0.55),
            )


class S2(ConceptScene):
    def construct(self):
        with self.beat("s2b1") as b:
            ghost, plane = grids()
            i_hat, j_hat = basis()
            square = unit_square()
            self.play(FadeIn(ghost), FadeIn(plane), FadeIn(i_hat), FadeIn(j_hat), run_time=b.t(0.2))
            self.heading("Follow one square", index=2)
            area = Eq(r"\text{area}", "=", "1", size=50).to_corner(UR, buff=0.7)
            area_card = plate(area)
            self.add(square)
            self.bring_to_front(i_hat, j_hat)
            self.play(DrawBorderThenFill(square), run_time=b.t(0.3))
            self.play(FadeIn(area_card), Write(area), run_time=b.t(0.2))

        with self.beat("s2b2") as b:
            self.play(
                ApplyMatrix(M, plane),
                ApplyMatrix(M, square),
                Transform(i_hat, arrow_to(2, 0, POSITIVE)),
                Transform(j_hat, arrow_to(1, 1.5, NEGATIVE)),
                run_time=b.t(0.5),
            )
            three = Eq("3", size=50).move_to(area[2])
            self.play(Transform(area[2], three), run_time=b.t(0.2))

        with self.beat("s2b3") as b:
            det = Eq(r"\det", "=", "3", size=50)
            det[2].set_color(ACCENT)
            det.next_to(area_card, DOWN, buff=0.45).align_to(area, RIGHT)
            box = highlight_box(det[2], color=ACCENT)
            self.play(FadeIn(plate(det, box)), Write(det), run_time=b.t(0.3))
            self.play(Create(box), Indicate(square, color=HIGHLIGHT, scale_factor=1.04), run_time=b.t(0.3))


class S3(ConceptScene):
    def construct(self):
        with self.beat("s3b1") as b:
            ghost, plane = grids()
            self.play(FadeIn(ghost), FadeIn(plane), run_time=b.t(0.2))
            self.heading("Every area, the same factor", index=3)
            circle = Circle(radius=0.55, color=SECONDARY, fill_opacity=0.35, stroke_width=3).move_to([-1.8, 1.0, 0])
            triangle = Polygon([0.8, -0.6, 0], [2.0, -0.6, 0], [1.2, -1.7, 0], color=SECONDARY, fill_opacity=0.35, stroke_width=3)
            small = Square(0.7, color=SECONDARY, fill_opacity=0.35, stroke_width=3).move_to([-1.6, -1.2, 0])
            shapes = VGroup(circle, triangle, small)
            self.play(LaggedStart(*[DrawBorderThenFill(s) for s in shapes], lag_ratio=0.25), run_time=b.t(0.5))

        with self.beat("s3b2") as b:
            self.play(ApplyMatrix(M, plane), ApplyMatrix(M, shapes), run_time=b.t(0.45))
            tags = VGroup()
            for shape in shapes:
                tag = Eq(r"\times 3", size=40, color=ACCENT).next_to(shape, UR, buff=0.05)
                tags.add(VGroup(plate(tag), tag))
            self.play(LaggedStart(*[FadeIn(t, scale=0.8) for t in tags], lag_ratio=0.2), run_time=b.t(0.3))


class S4(ConceptScene):
    def construct(self):
        with self.beat("s4b1") as b:
            self.heading("Zero and negative", index=4)
            general = Eq(r"\det\begin{bmatrix} a & b \\ c & d \end{bmatrix}", "=", "ad - bc", size=54)
            ours = Eq(r"\det\begin{bmatrix} 2 & 1 \\ 0 & 1.5 \end{bmatrix}", "=", r"2 \cdot 1.5 - 1 \cdot 0", "=", "3", size=54)
            ours[4].set_color(ACCENT)
            VGroup(general, ours).arrange(DOWN, buff=0.7).move_to(0.2 * UP)
            self.play(Write(general), run_time=b.t(0.3))
            self.play(Write(ours), run_time=b.t(0.4))

        with self.beat("s4b2") as b:
            self.clear_stage(run_time=0.5)
            ghost, plane = grids()
            i_hat, j_hat = basis()
            square = unit_square()
            self.play(FadeIn(ghost), FadeIn(plane), FadeIn(square), FadeIn(i_hat), FadeIn(j_hat), run_time=b.t(0.12))
            plane.save_state()
            square.save_state()
            Z = [[1, 2], [0.5, 1]]
            self.play(
                ApplyMatrix(Z, plane),
                ApplyMatrix(Z, square),
                Transform(i_hat, arrow_to(1, 0.5, POSITIVE)),
                Transform(j_hat, arrow_to(2, 1, NEGATIVE)),
                run_time=b.t(0.35),
            )
            zero = Eq(r"\det", "=", "0", size=50).to_corner(UR, buff=0.7)
            zero[2].set_color(ACCENT)
            zero_card = VGroup(plate(zero), zero)
            self.play(FadeIn(zero_card), run_time=b.t(0.12))
            self.play(
                Restore(plane),
                Restore(square),
                Transform(i_hat, arrow_to(1, 0, POSITIVE)),
                Transform(j_hat, arrow_to(0, 1, NEGATIVE)),
                FadeOut(zero_card),
                run_time=b.t(0.25),
            )

        with self.beat("s4b3") as b:
            F = [[1, 0.5], [0, -1]]
            self.play(
                ApplyMatrix(F, plane),
                ApplyMatrix(F, square),
                Transform(j_hat, arrow_to(0.5, -1, NEGATIVE)),
                run_time=b.t(0.45),
            )
            negative = Eq(r"\det", "=", "-1", size=50)
            negative[2].set_color(ACCENT)
            note = Label("orientation flipped", size=28, italic=True)
            stack = VGroup(negative, note).arrange(DOWN, buff=0.2).to_corner(UR, buff=0.7)
            self.play(FadeIn(plate(stack)), Write(negative), FadeIn(note, shift=0.1 * UP), run_time=b.t(0.25))
