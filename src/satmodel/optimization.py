"""Generic optimization helpers used by tuning examples."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np


@dataclass
class OptimizationResult:
    x: np.ndarray
    score: float
    history: np.ndarray


def _bounds(bounds):
    lo = np.asarray(bounds[0], dtype=float).reshape(-1)
    hi = np.asarray(bounds[1], dtype=float).reshape(-1)
    if lo.shape != hi.shape or np.any(hi < lo):
        raise ValueError("bounds must contain matching lower and upper vectors")
    return lo, hi


def reflect_bounds(value, lo, hi):
    x = np.asarray(value, dtype=float)
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    span = hi - lo
    wrapped = np.mod(x - lo, 2.0 * np.maximum(span, 1e-12))
    return lo + np.where(wrapped <= span, wrapped, 2.0 * span - wrapped)


class GridSearchOptimizer:
    def __init__(self, points_per_dim: int = 6):
        self.points_per_dim = int(points_per_dim)

    def optimize(self, objective, bounds) -> OptimizationResult:
        lo, hi = _bounds(bounds)
        points = [np.asarray(item, dtype=float) for item in product(*[np.linspace(a, b, self.points_per_dim) for a, b in zip(lo, hi)])]
        scores = np.asarray([float(objective(point)) for point in points], dtype=float)
        idx = int(np.argmin(scores))
        return OptimizationResult(points[idx], float(scores[idx]), np.minimum.accumulate(scores))


class RandomSearchOptimizer:
    def __init__(self, iterations: int = 40, seed: int = 0):
        self.iterations = int(iterations)
        self.seed = int(seed)

    def optimize(self, objective, bounds) -> OptimizationResult:
        lo, hi = _bounds(bounds)
        rng = np.random.RandomState(self.seed)
        samples = lo + rng.rand(max(1, self.iterations), lo.size) * (hi - lo)
        scores = np.asarray([float(objective(point)) for point in samples], dtype=float)
        idx = int(np.argmin(scores))
        return OptimizationResult(samples[idx], float(scores[idx]), np.minimum.accumulate(scores))


class NelderMeadOptimizer:
    def __init__(self, iterations: int = 80, step_fraction: float = 0.15):
        self.iterations = int(iterations)
        self.step_fraction = float(step_fraction)

    def optimize(self, objective, bounds) -> OptimizationResult:
        lo, hi = _bounds(bounds)
        n = lo.size
        simplex = [0.5 * (lo + hi)]
        step = self.step_fraction * np.maximum(hi - lo, 1e-6)
        for axis in range(n):
            point = simplex[0].copy()
            point[axis] += step[axis]
            simplex.append(reflect_bounds(point, lo, hi))
        simplex = np.asarray(simplex, dtype=float)
        scores = np.asarray([float(objective(point)) for point in simplex], dtype=float)
        history = [float(np.min(scores))]
        for _ in range(max(1, self.iterations)):
            order = np.argsort(scores)
            simplex, scores = simplex[order], scores[order]
            centroid = np.mean(simplex[:-1], axis=0)
            reflected = reflect_bounds(centroid + (centroid - simplex[-1]), lo, hi)
            reflected_score = float(objective(reflected))
            if scores[0] <= reflected_score < scores[-2]:
                simplex[-1], scores[-1] = reflected, reflected_score
            elif reflected_score < scores[0]:
                expanded = reflect_bounds(centroid + 2.0 * (reflected - centroid), lo, hi)
                expanded_score = float(objective(expanded))
                simplex[-1], scores[-1] = (
                    (expanded, expanded_score) if expanded_score < reflected_score else (reflected, reflected_score)
                )
            else:
                contracted = reflect_bounds(centroid + 0.5 * (simplex[-1] - centroid), lo, hi)
                contracted_score = float(objective(contracted))
                if contracted_score < scores[-1]:
                    simplex[-1], scores[-1] = contracted, contracted_score
                else:
                    simplex[1:] = reflect_bounds(simplex[0] + 0.5 * (simplex[1:] - simplex[0]), lo, hi)
                    scores[1:] = [float(objective(point)) for point in simplex[1:]]
            history.append(float(np.min(scores)))
        idx = int(np.argmin(scores))
        return OptimizationResult(simplex[idx].copy(), float(scores[idx]), np.asarray(history, dtype=float))


class SimulatedAnnealingOptimizer:
    def __init__(self, iterations: int = 100, temperature: float = 1.0, decay: float = 0.97, seed: int = 0):
        self.iterations = int(iterations)
        self.temperature = float(temperature)
        self.decay = float(decay)
        self.seed = int(seed)

    def optimize(self, objective, bounds) -> OptimizationResult:
        lo, hi = _bounds(bounds)
        rng = np.random.RandomState(self.seed)
        current = lo + rng.rand(lo.size) * (hi - lo)
        current_score = float(objective(current))
        best, best_score = current.copy(), current_score
        temperature = self.temperature
        history = [best_score]
        for _ in range(max(1, self.iterations)):
            proposal = reflect_bounds(current + 0.2 * (hi - lo) * rng.randn(lo.size), lo, hi)
            proposal_score = float(objective(proposal))
            delta = proposal_score - current_score
            if delta <= 0.0 or rng.rand() < np.exp(-delta / max(temperature, 1e-12)):
                current, current_score = proposal, proposal_score
                if current_score < best_score:
                    best, best_score = current.copy(), current_score
            history.append(best_score)
            temperature *= self.decay
        return OptimizationResult(best, float(best_score), np.asarray(history, dtype=float))


class PSOOptimizer:
    def __init__(self, iterations: int = 30, swarm_size: int = 12, seed: int = 0):
        self.iterations = int(iterations)
        self.swarm_size = int(swarm_size)
        self.seed = int(seed)

    def optimize(self, objective, bounds) -> OptimizationResult:
        lo, hi = _bounds(bounds)
        rng = np.random.RandomState(self.seed)
        count = max(4, self.swarm_size)
        x = lo + rng.rand(count, lo.size) * (hi - lo)
        velocity = 0.05 * (hi - lo) * rng.randn(count, lo.size)
        personal = x.copy()
        scores = np.asarray([float(objective(point)) for point in x], dtype=float)
        personal_scores = scores.copy()
        idx = int(np.argmin(scores))
        global_best, global_score = personal[idx].copy(), float(scores[idx])
        history = [global_score]
        for _ in range(max(1, self.iterations)):
            velocity = (
                0.7 * velocity
                + 1.4 * rng.rand(count, lo.size) * (personal - x)
                + 1.4 * rng.rand(count, lo.size) * (global_best - x)
            )
            x = reflect_bounds(x + velocity, lo, hi)
            scores = np.asarray([float(objective(point)) for point in x], dtype=float)
            improved = scores < personal_scores
            personal[improved], personal_scores[improved] = x[improved], scores[improved]
            idx = int(np.argmin(personal_scores))
            if personal_scores[idx] < global_score:
                global_best, global_score = personal[idx].copy(), float(personal_scores[idx])
            history.append(global_score)
        return OptimizationResult(global_best, global_score, np.asarray(history, dtype=float))
