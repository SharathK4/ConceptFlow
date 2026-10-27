```python
from manim import *
from cfkit import *
import numpy as np


def f(x):
    return 0.3 * x**2 + 0.4


def position_axes():
    axes = cf_axes(x_range=(0, 5, 1), y_range=(0, 8, 2), x_length=6.2, y_length=4.4, numbers=False)
    axes.to_edge(LEFT, buff=0.8).shift(0.35 * DOWN)
    labels = axes.get_axis_labels(MathTex("x", font_size=32, color=MUTED), MathTex("f(x)", font_size=32, color=MUTED))
    return axes, labels


class S1(ConceptScene):
    def construct(self):
        with self.beat("s1b1") as b:
            self.title_card("The Derivative", "speed at a single instant", run_time=b.t(0.7))

        with self.beat("s1b2") as b:
            self.clear_stage(run_time=0.5)
            road = NumberLine(
                x_range=[0, 10, 1],
                length=10,
                color=MUTED,
                include_numbers=True,
                font_size=24,
                decimal_number_config={"color": MUTED, "num_decimal_places": 0},
            ).move_to(UP * 1.3)
            car = Dot(road.n2p(0), color=HIGHLIGHT, radius=0.11)
            trail = always_redraw(
                lambda: Line(road.n2p(0), car.get_center() + RIGHT * 0.001, color=PRIMARY, stroke_width=7)
            )
            self.play(Create(road), FadeIn(car, scale=0.5), run_time=b.t(0.2))
            self.add(trail, car)
            self.play(car.animate.move_to(road.n2p(6)), run_time=b.t(0.3))
            trail.clear_updaters()
            brace = Brace(trail, UP, buff=0.18, color=MUTED)
            d_label = MathTex(r"\Delta d", font_size=40, color=MUTED).next_to(brace, UP, buff=0.12)
            speed = Eq(r"\text{average speed}", "=", r"\frac{\Delta d}{\Delta t}", size=56).move_to(DOWN * 1.1)
            self.play(GrowFromCenter(brace), FadeIn(d_label, shift=0.1 * UP), run_time=b.t(0.15))
            self.play(Write(speed), run_time=b.t(0.25))

        with self.beat("s1b3") as b:
            self.play(
                trail.animate.put_start_and_end_on(road.n2p(5.97), road.n2p(6)),
                FadeOut(brace),
                FadeOut(d_label),
                run_time=b.t(0.2),
            )
            instant = Eq(r"\text{speed at an instant}", "=", r"\frac{0}{0}", r"\;?", size=56).move_to(speed)
            instant[2].set_color(ACCENT)
            self.play(
                ReplacementTransform(speed[0], instant[0]),
                ReplacementTransform(speed[1], instant[1]),
                ReplacementTransform(speed[2], instant[2]),
                FadeIn(instant[3], shift=0.1 * LEFT),
                run_time=b.t(0.3),
            )
            self.play(Circumscribe(instant[2], Circle, color=ACCENT, buff=0.2), run_time=b.t(0.2))


class S2(ConceptScene):
    def construct(self):
        x0, h = 1.5, 2.0

        with self.beat("s2b1") as b:
            self.heading("Slopes of secant lines", index=2)
            axes, labels = position_axes()
            curve = axes.plot(f, x_range=(0, 4.6), color=PRIMARY, stroke_width=4)
            self.play(Create(axes), FadeIn(labels), run_time=b.t(0.3))
            self.play(Create(curve), run_time=b.t(0.45))

        with self.beat("s2b2") as b:
            P, Q = axes.c2p(x0, f(x0)), axes.c2p(x0 + h, f(x0 + h))
            corner = axes.c2p(x0 + h, f(x0))
            p_dot = Dot(P, color=HIGHLIGHT, radius=0.08)
            q_dot = Dot(Q, color=SECONDARY, radius=0.08)
            drops = VGroup(
                axes.get_vertical_line(P, color=FAINT, stroke_width=2),
                axes.get_vertical_line(Q, color=FAINT, stroke_width=2),
            )
            ticks = VGroup(
                MathTex("x", font_size=32, color=HIGHLIGHT).next_to(axes.c2p(x0, 0), DOWN, buff=0.15),
                MathTex("x+h", font_size=32, color=SECONDARY).next_to(axes.c2p(x0 + h, 0), DOWN, buff=0.15),
            )
            slope = (f(x0 + h) - f(x0)) / h
            secant = axes.plot(lambda x: f(x0) + slope * (x - x0), x_range=(0.85, 4.5), color=ACCENT, stroke_width=3)
            run = DashedLine(P, corner, color=MUTED, stroke_width=2.5)
            rise = DashedLine(corner, Q, color=MUTED, stroke_width=2.5)
            h_label = MathTex("h", font_size=34, color=MUTED).next_to(run, DOWN, buff=0.12)
            df_label = MathTex(r"\Delta f", font_size=34, color=MUTED).next_to(rise, RIGHT, buff=0.12)
            self.play(FadeIn(p_dot, scale=0.5), FadeIn(q_dot, scale=0.5), run_time=b.t(0.15))
            self.play(Create(drops), FadeIn(ticks, shift=0.1 * UP), run_time=b.t(0.2))
            self.play(Create(run), Create(rise), FadeIn(h_label), FadeIn(df_label), run_time=b.t(0.2))
            self.play(Create(secant), run_time=b.t(0.25))

        with self.beat("s2b3") as b:
            formula = Eq(r"\text{slope}", "=", r"\frac{f(x+h) - f(x)}{h}", size=46).move_to(RIGHT * 3.6 + UP * 1.3)
            self.play(Indicate(df_label, color=HIGHLIGHT), Indicate(h_label, color=HIGHLIGHT), run_time=b.t(0.2))
            self.play(Write(formula), run_time=b.t(0.35))
            self.play(Circumscribe(formula, color=HIGHLIGHT, buff=0.15), run_time=b.t(0.2))


class S3(ConceptScene):
    def construct(self):
        x0 = 1.5
        h = ValueTracker(2.0)

        def slope_of(value):
            return (f(x0 + value) - f(x0)) / value

        with self.beat("s3b1") as b:
            self.heading("Let h shrink", index=3)
            axes, labels = position_axes()
            curve = axes.plot(f, x_range=(0, 4.6), color=PRIMARY, stroke_width=4)
            self.play(FadeIn(axes), FadeIn(labels), Create(curve), run_time=b.t(0.15))

            def dot_radius():
                return 0.08 * self.camera.frame.width / config.frame_width

            p_dot = always_redraw(lambda: Dot(axes.c2p(x0, f(x0)), color=HIGHLIGHT, radius=dot_radius()))
            q_dot = always_redraw(
                lambda: Dot(axes.c2p(x0 + h.get_value(), f(x0 + h.get_value())), color=SECONDARY, radius=dot_radius())
            )
            secant = always_redraw(
                lambda: Line(
                    axes.c2p(x0 - 0.7, f(x0) - 0.7 * slope_of(h.get_value())),
                    axes.c2p(x0 + 2.8, f(x0) + 2.8 * slope_of(h.get_value())),
                    color=ACCENT,
                    stroke_width=3,
                )
            )
            h_value = DecimalNumber(2.0, num_decimal_places=2, font_size=44, color=SECONDARY)
            m_value = DecimalNumber(slope_of(2.0), num_decimal_places=2, font_size=44, color=ACCENT)
            readout = VGroup(
                VGroup(MathTex("h", "=", font_size=44), h_value).arrange(RIGHT, buff=0.2),
                VGroup(MathTex(r"\text{slope}", "=", font_size=44), m_value).arrange(RIGHT, buff=0.2),
            ).arrange(DOWN, aligned_edge=LEFT, buff=0.35).move_to(RIGHT * 3.6 + UP * 1.0)
            h_value.add_updater(lambda d: d.set_value(h.get_value()))
            m_value.add_updater(lambda d: d.set_value(slope_of(h.get_value())))
            self.play(FadeIn(p_dot), FadeIn(q_dot), Create(secant), FadeIn(readout, shift=0.1 * UP), run_time=b.t(0.2))
            self.play(h.animate.set_value(0.4), run_time=b.t(0.5))

        with self.beat("s3b2") as b:
            self.play(h.animate.set_value(0.05), run_time=b.t(0.2))
            self.play(self.camera.frame.animate.scale(0.1).move_to(axes.c2p(x0, f(x0))), run_time=b.t(0.3))
            self.play(self.camera.frame.animate.scale(10).move_to(ORIGIN), run_time=b.t(0.3))

        with self.beat("s3b3") as b:
            secant.clear_updaters()
            tangent = Line(
                axes.c2p(x0 - 0.7, f(x0) - 0.7 * 0.9),
                axes.c2p(x0 + 2.8, f(x0) + 2.8 * 0.9),
                color=ACCENT,
                stroke_width=4,
            )
            tangent_label = Label("tangent line", size=26, color=ACCENT, italic=True)
            tangent_label.next_to(tangent.point_from_proportion(0.8), DR, buff=0.15)
            definition = Eq("f'(x)", "=", r"\lim_{h \to 0}", r"\frac{f(x+h) - f(x)}{h}", size=46).move_to(RIGHT * 3.55 + UP * 1.0)
            definition[2].set_color(ACCENT)
            self.play(
                ReplacementTransform(secant, tangent),
                FadeOut(q_dot),
                FadeOut(readout),
                run_time=b.t(0.2),
            )
            self.play(ShowCreationThenFadeOut(tangent.copy().set_color(HIGHLIGHT)), FadeIn(tangent_label), run_time=b.t(0.15))
            self.play(Write(definition), run_time=b.t(0.3))
            self.play(Create(highlight_box(definition, color=ACCENT)), run_time=b.t(0.15))


class S4(ConceptScene):
    def construct(self):
        with self.beat("s4b1") as b:
            self.heading("An example: x squared", index=4)
            steps = VGroup(
                Eq(r"\frac{(x+h)^2 - x^2}{h}", size=48),
                Eq(r"= \frac{x^2 + 2xh + h^2 - x^2}{h}", size=48),
                Eq(r"= \frac{2xh + h^2}{h}", size=48),
                Eq(r"= 2x + h", size=48),
            ).arrange(DOWN, aligned_edge=LEFT, buff=0.32)
            steps.move_to(LEFT * 1.6 + UP * 0.25)
            self.play(Write(steps[0]), run_time=b.t(0.25))
            self.play(FadeIn(steps[1], shift=0.15 * DOWN), run_time=b.t(0.25))
            self.play(FadeIn(steps[2], shift=0.15 * DOWN), run_time=b.t(0.25))

        with self.beat("s4b2") as b:
            self.play(FadeIn(steps[3], shift=0.15 * DOWN), run_time=b.t(0.25))
            limit = Eq(r"\xrightarrow{\;h \to 0\;}", "2x", size=48).next_to(steps[3], RIGHT, buff=0.35)
            limit[1].set_color(ACCENT)
            self.play(Write(limit), run_time=b.t(0.3))
            self.play(Create(highlight_box(limit[1], color=ACCENT)), run_time=b.t(0.2))

        with self.beat("s4b3") as b:
            result = Eq("f'(x)", "=", "2x", size=54).move_to(RIGHT * 3.9 + UP * 2.2)
            result[2].set_color(ACCENT)
            self.clear_stage(limit, run_time=b.t(0.08))
            self.play(
                FadeOut(limit[0]),
                Transform(limit[1], result[2]),
                FadeIn(result[0]),
                FadeIn(result[1]),
                run_time=b.t(0.12),
            )
            axes = cf_axes(x_range=(-2, 2, 1), y_range=(-1, 4, 1), x_length=6.0, y_length=4.6, numbers=False)
            axes.to_edge(LEFT, buff=0.9).shift(0.45 * DOWN)
            parabola = axes.plot(lambda x: x**2, x_range=(-1.95, 1.95), color=PRIMARY, stroke_width=4)
            t = ValueTracker(-1.5)
            point = always_redraw(lambda: Dot(axes.c2p(t.get_value(), t.get_value() ** 2), color=HIGHLIGHT, radius=0.08))
            tangent = always_redraw(
                lambda: axes.plot(
                    lambda x: t.get_value() ** 2 + 2 * t.get_value() * (x - t.get_value()),
                    x_range=(t.get_value() - 0.6, t.get_value() + 0.6),
                    color=ACCENT,
                    stroke_width=3.5,
                )
            )
            x_value = DecimalNumber(-1.5, num_decimal_places=2, font_size=42, color=HIGHLIGHT)
            s_value = DecimalNumber(-3.0, num_decimal_places=2, font_size=42, color=ACCENT)
            readout = VGroup(
                VGroup(MathTex("x", "=", font_size=42), x_value).arrange(RIGHT, buff=0.2),
                VGroup(MathTex(r"\text{slope}", "=", font_size=42), s_value).arrange(RIGHT, buff=0.2),
            ).arrange(DOWN, aligned_edge=LEFT, buff=0.3).next_to(result, DOWN, buff=0.7).align_to(result, LEFT)
            x_value.add_updater(lambda d: d.set_value(t.get_value()))
            s_value.add_updater(lambda d: d.set_value(2 * t.get_value()))
            self.play(Create(axes), Create(parabola), run_time=b.t(0.2))
            self.play(FadeIn(point), Create(tangent), FadeIn(readout, shift=0.1 * UP), run_time=b.t(0.12))
            self.play(t.animate.set_value(1.5), run_time=b.t(0.45))
```

NOTES:
- Scenes 2 and 3 share one position curve and axes so the secant picture carries over between them.
- Dots in S3 scale with the camera frame so they stay small during the zoom into P.
- S4 ends on a live tangent whose slope readout is 2x, landing the takeaway visually.
