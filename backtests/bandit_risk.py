import numpy as np
import json

class LinUCBRisk:
    def __init__(self, n_arms=3, alpha=1.0, feature_dim=None):
        self.n_arms = n_arms
        self.alpha = alpha
        self.feature_dim = feature_dim
        self.A = [np.identity(feature_dim) for _ in range(n_arms)]
        self.b = [np.zeros((feature_dim, 1)) for _ in range(n_arms)]

    def select_arm(self, features):
        p = np.zeros(self.n_arms)
        for arm in range(self.n_arms):
            A_inv = np.linalg.inv(self.A[arm])
            theta = A_inv @ self.b[arm]
            p[arm] = (theta.T @ features).item() + self.alpha * np.sqrt(features.T @ A_inv @ features).item()
        return np.argmax(p)

    def update(self, arm, features, reward):
        self.A[arm] += features @ features.T
        self.b[arm] += reward * features

    def save(self, filename):
        data = {
            'n_arms': self.n_arms,
            'alpha': self.alpha,
            'feature_dim': self.feature_dim,
            'A': [a.tolist() for a in self.A],
            'b': [b.tolist() for b in self.b]
        }
        with open(filename, 'w') as f:
            json.dump(data, f)

    @classmethod
    def load(cls, filename):
        with open(filename, 'r') as f:
            data = json.load(f)
        bandit = cls(n_arms=data['n_arms'], alpha=data['alpha'], feature_dim=data['feature_dim'])
        bandit.A = [np.array(a) for a in data['A']]
        bandit.b = [np.array(b) for b in data['b']]
        return bandit
