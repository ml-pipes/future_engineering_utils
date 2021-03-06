# AUTOGENERATED! DO NOT EDIT! File to edit: 00_cluster.ipynb (unless otherwise specified).

__all__ = ['load_embedding', 'BayesClusterTrainer']

# Cell
from functools import partial
import hdbscan
from hyperopt import hp
from hyperopt import fmin, tpe, space_eval
from hyperopt import Trials
from hyperopt import STATUS_OK
import numpy as np
import os
import pandas as pd
from sklearn.preprocessing import normalize
from sklearn.model_selection import RandomizedSearchCV
from sklearn.metrics import make_scorer
from sklearn.metrics.cluster import adjusted_rand_score
from sklearn.metrics.cluster import homogeneity_completeness_v_measure
from sklearn.metrics.cluster import homogeneity_score, v_measure_score, silhouette_score
from sklearn.metrics.cluster import completeness_score, adjusted_mutual_info_score
import time
import umap


# Cell

def load_embedding(path):
    """
    dataloader for saved embeddings. Load from numpy file
    """
    emb = np.load(path)

    return emb


# Cell
class BayesClusterTrainer():
    """
    A trainer for cluster optimization runs
    Inputs:
        `space`: `dict` containing relevant parameter spaces for `hdbscan` and `umap`

    """

    def __init__(self, space, cost_fn_params, embeddings, labels, optimize='default' , *args, **kwargs):
        self.space = space
        self.cost_fn_params = cost_fn_params

        self.embeddings = embeddings
        self.labels = labels

        self.optimize = optimize

        self.logs = []

        self.run = dict()

    def generate_clusters(self, embeddings,
                        min_cluster_size,
                        cluster_selection_epsilon,
                        cluster_selection_method,
                        metric,
                        n_neighbors,
                        n_components,
                        random_state = 42):
        """
        Generate HDBSCAN cluster object after reducing embedding dimensionality with UMAP
        """

        umap_embeddings = (umap.UMAP(n_neighbors=n_neighbors,
                                    n_components=n_components,
                                    metric='cosine',
                                    random_state=random_state)
                                    .fit_transform(embeddings))

        clusters = hdbscan.HDBSCAN(min_cluster_size = min_cluster_size,
                                   metric=metric, cluster_selection_epsilon = cluster_selection_epsilon,
                                   cluster_selection_method=cluster_selection_method,
                                   gen_min_span_tree=True).fit(umap_embeddings)

        return clusters

    def objective(self, params, embeddings, labels):
        """
        Objective function for hyperopt to minimize, which incorporates constraints
        on the number of clusters we want to identify
        """

        clusters = self.generate_clusters(embeddings,
                                     n_neighbors = params['n_neighbors'],
                                     n_components = params['n_components'],
                                     min_cluster_size = params['min_cluster_size'],
                                     cluster_selection_epsilon = params['cluster_selection_epsilon'],
                                     metric = params['metric'],
                                     cluster_selection_method = params['cluster_selection_method'],
                                     random_state = 42)

        cost = self.score_clusters(clusters, labels, params)

        loss = cost

        return {'loss': loss, 'status': STATUS_OK}


    def score_clusters(self, clusters, y, params):
        """
        Returns the label count and cost of a given cluster supplied from running hdbscan
        """

        val = clusters.relative_validity_

        #prevent pers from getting NaN value if no clusters exist
        if len(clusters.cluster_persistence_)==0:
            pers = 0.
        else:
            pers = clusters.cluster_persistence_.mean(0)

        prob = clusters.probabilities_.mean(0)

        penalty = (clusters.labels_ == -1).sum() / len(clusters.labels_)

        if len(clusters.outlier_scores_)==0:
            outlier = 0.0
        else:
            outlier = clusters.outlier_scores_.mean(0)


        cluster_size = len(np.unique(clusters.labels_))


        self.run['relative_validity'] = val
        self.run['probability'] = prob
        self.run['persistence'] = pers
        self.run['penalty'] = penalty
        self.run['outlier'] = outlier
        self.run['cluster_size'] = cluster_size

        fns = [adjusted_rand_score, homogeneity_completeness_v_measure, homogeneity_score, v_measure_score, completeness_score, adjusted_mutual_info_score]

        for fn in fns:
            print(f"{fn.__name__} : {fn(clusters.labels_, y)}")
            self.run[f'{fn.__name__}'] = fn(clusters.labels_, y)
        print("-"*20)


        if self.optimize == 'rand_score':

            score = -1. * adjusted_rand_score(clusters.labels_, y)
            self.run['score'] = score

            self.run = {**self.run, **params}

            self.logs.append(self.run.copy())
            self.run.clear()

            print(f"SCORE: {score}")

            return score

        elif self.optimize == 'default':

            print(f'val: {val}')
            print(f'pers: {pers}')
            print(f'prob: {prob}')
            print(f'penalty: {penalty}')
            print(f'outlier: {outlier}')
            print(f'cluster size: {cluster_size}')

            val_w = self.cost_fn_params['val_w']
            prob_w = self.cost_fn_params['prob_w']
            pers_w = self.cost_fn_params['pers_w']
            penalty_w = self.cost_fn_params['penalty_w']
            outlier_w = self.cost_fn_params['outlier_w']

            score = -1*(val_w * val + prob_w * prob + pers_w * pers) + (penalty_w * penalty +  outlier_w * outlier)

            self.run['score'] = score

            self.run = {**self.run, **self.cost_fn_params, **params}

            self.logs.append(self.run.copy())
            self.run.clear()

            print(f"SCORE: {score}")

            return score


    def train(self, max_evals=100, algo=tpe.suggest):
        """
        Perform bayesian search on hyperopt hyperparameter space to minimize objective function
        """

        trials = Trials()
        fmin_objective = partial(self.objective, embeddings=self.embeddings, labels=self.labels)
        best = fmin(fmin_objective,
                    space = self.space,
                    algo=algo,
                    max_evals=max_evals,
                    trials=trials)

        best_params = space_eval(self.space, best)
        print ('best:')
        print (best_params)
        print("-"*20)
        print("-"*20)

        best_clusters = self.generate_clusters(self.embeddings,
                                         n_neighbors = best_params['n_neighbors'],
                                         n_components = best_params['n_components'],
                                         min_cluster_size = best_params['min_cluster_size'],
                                         cluster_selection_epsilon = best_params['cluster_selection_epsilon'],
                                         metric = best_params['metric'],
                                         cluster_selection_method = best_params['cluster_selection_method']
                                         )


        self.best_params = best_params
        self.best_clusters = best_clusters
        self.trials = trials

        print(f'Finished training!')

    def save_logs_to_csv(self, path, dataset=None):
        """
        save logs to a csv file. Provide the path, optionally provide a dataset name. Creates new column.
        """
        if self.optimize == 'default':
            cols = ['adjusted_rand_score', 'homogeneity_completeness_v_measure',
           'homogeneity_score', 'v_measure_score', 'completeness_score',
           'adjusted_mutual_info_score', 'relative_validity', 'probability',
           'persistence', 'penalty', 'outlier', 'score', 'cluster_size', 'val_w',
           'prob_w', 'pers_w', 'penalty_w', 'outlier_w',
           'cluster_selection_epsilon', 'cluster_selection_method', 'metric',
           'min_cluster_size', 'n_components', 'n_neighbors']
        elif self.optimize == 'rand_score':
            cols = ['relative_validity', 'probability', 'persistence', 'penalty', 'outlier', 'cluster_size',
                    'adjusted_rand_score', 'homogeneity_completeness_v_measure', 'homogeneity_score', 'v_measure_score',
                    'completeness_score', 'adjusted_mutual_info_score', 'score']

        df = pd.DataFrame(columns=cols)

        df = df.append(self.logs)

        if dataset!=None:
            df['dataset'] = dataset
        #df.to_csv(path, index=False)
        return df