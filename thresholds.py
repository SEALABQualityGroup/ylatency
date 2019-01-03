from functools import reduce
from pyspark.sql.functions import col
from sklearn.metrics import roc_curve
from sklearn.cluster import KMeans
import numpy as np
from abc import ABC, abstractmethod


class Selector(ABC):
    def __init__(self, traces, backends, frontend, frontendSLA):
        self.backends = backends
        self.thresholdsDict = {}
        self.tprDict = {}
        self.fprDict = {}
        self._createThresholdsDict(traces, backends, frontend, frontendSLA)

    def _createThresholdsDict(self, traces, backends, frontend, frontendSLA):
        maxs = {c: traces.select(c).rdd.max()[0]
                for c in backends}
        mins = {c: traces.select(c).rdd.min()[0]
                for c in backends}
        normalizedTrace = reduce(lambda df, c: df.withColumn(c, (col(c) - mins[c]) / (maxs[c] - mins[c])),
                                 backends,
                                 traces)
        y = [1 if row[0] > frontendSLA else 0
             for row in traces.select(frontend).collect()]
        for aBackend in backends:
            scores = [row[0] for row in normalizedTrace.select(aBackend).collect()]
            fpr, tpr, thresholds = roc_curve(y, scores)
            self.thresholdsDict[aBackend] = thresholds[:0:-1]
            self.fprDict[aBackend] = [float(fpr_) for fpr_ in fpr[:0:-1]]
            self.tprDict[aBackend] = [float(tpr_) for tpr_ in tpr[:0:-1]]

    @abstractmethod
    def select(self, k):
        pass


class KMeansSelector(Selector):
    def select(self, k):
        thresholdDict = {}
        for aBackend in self.backends:
            thresholds = self.thresholdsDict[aBackend]
            if k + 1 >= (len(thresholds)):
                thresholdDict[aBackend] = thresholds
                continue
            tpr = self.tprDict[aBackend]
            fpr = self.fprDict[aBackend]
            X = list(zip(tpr[1:], fpr[1:]))
            distances = KMeans(n_clusters=k, random_state=0).fit_transform(X)
            indices = [np.argsort(distances[:, i])[0] for i in range(k)]
            thresholdDict[aBackend] = [thresholds[0]] + sorted(thresholds[i + 1] for i in indices)
        return thresholdDict


class RandSelector(Selector):
    def select(self, k):
        thresholdDict = {}
        for aBackend in self.backends:
            thresholds = self.thresholdsDict[aBackend]
            if k + 1 >= (len(thresholds)):
                thresholdDict[aBackend] = thresholds
            else:
                selected = np.random.choice(thresholds[1:], size=k, replace=False)
                thresholdDict[aBackend] = sorted([thresholds[0]] + list(selected))
        return thresholdDict