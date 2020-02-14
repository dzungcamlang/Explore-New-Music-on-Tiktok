import os
import numpy as np
import pandas as pd
import librosa
import warnings
from scipy import stats
import json
from pyspark.sql.session import SparkSession
from pyspark.sql.types import FloatType
import pyspark.sql.functions as f
import time
import pyspark
import boto3
from pyspark import SparkContext, SparkConf
from pyspark.sql.session import SparkSession
from pyspark.sql import DataFrameReader, SQLContext
#import faiss
import pyarrow.parquet as pq

class SimilaritySearch(object):
    def __init__(self):
        self.path = './music-vector-by-genres/genre='

    def get_vect_df(self, top_genre_id, spark):
        music_vect = spark.read\
        .load(self.path+top_genre_id)\
        .select('track_id','features').to_pandas()
        return music_vect    

    def cal_index(self, new_df, vec_df):
        vec = new_df.features[0]
        max_df = vec_df['features']\
                 .apply(lambda x: np.array(x) - np.array(vec))\
                 .apply(lambda x: sum(x))
        max_df = pd.DataFrame(max_df)
        index = max_df.sort_values('features', ascending = True)\
                 .head(5)\
                 .index

        return index

class MusicProcessor(object):
    def __init__(self, base_dir, filename):
        self.filename = filename
        self.path = os.path.join(base_dir, 'static', 'music', self.filename)

    def columns(self):
        feature_sizes = dict(zcr=1, chroma_stft=12, spectral_centroid=1, spectral_rolloff=1, mfcc=20)
        moments = ('mean', 'std', 'skew', 'kurtosis', 'median', 'min', 'max')

        columns1 = []
        for name, size in feature_sizes.items():
            for moment in moments:
                it = ((name, moment, '{:02d}'.format(i+1)) for i in range(size))
                columns1.extend(it)
        names = ('feature', 'statistics', 'number')
        columns1 = pd.MultiIndex.from_tuples(columns1, names=names)
        # More efficient to slice if indexes are sorted.
        return columns1.sort_values()

    def compute_features(self, AUDIO_DIR):
        index = self.columns()
        features = pd.Series(index=index, dtype=np.float32, name=AUDIO_DIR)
        # Catch warnings as exceptions (audioread leaks file descriptors)
        def feature_stats(name, values):
            features[name, 'mean'] = np.mean(values, axis=1)
            features[name, 'std'] = np.std(values, axis=1)
            features[name, 'skew'] = stats.skew(values, axis=1)
            features[name, 'kurtosis'] = stats.kurtosis(values, axis=1)
            features[name, 'median'] = np.median(values, axis=1)
            features[name, 'min'] = np.min(values, axis=1)
            features[name, 'max'] = np.max(values, axis=1)
        try:
            x, sr = librosa.load(AUDIO_DIR, sr=None, mono=True)  # kaiser_fast
            f = librosa.feature.zero_crossing_rate(x, frame_length=2048, hop_length=512)
            feature_stats('zcr', f)
            cqt = np.abs(librosa.cqt(x, sr=sr, hop_length=512, bins_per_octave=12,
                                      n_bins=7*12, tuning=None))
            assert cqt.shape[0] == 7 * 12
            assert np.ceil(len(x)/512) <= cqt.shape[1] <= np.ceil(len(x)/512)+1
            del cqt
            stft = np.abs(librosa.stft(x, n_fft=2048, hop_length=512))
            assert stft.shape[0] == 1 + 2048 // 2
            assert np.ceil(len(x)/512) <= stft.shape[1] <= np.ceil(len(x)/512)+1
            del x
            f = librosa.feature.chroma_stft(S=stft**2, n_chroma=12)
            feature_stats('chroma_stft', f)
            f = librosa.feature.spectral_centroid(S=stft)
            feature_stats('spectral_centroid', f)
            f = librosa.feature.spectral_rolloff(S=stft)
            feature_stats('spectral_rolloff', f)
            mel = librosa.feature.melspectrogram(sr=sr, S=stft**2)
            del stft
            f = librosa.feature.mfcc(S=librosa.power_to_db(mel), n_mfcc=20)
            feature_stats('mfcc', f)
        except Exception as e:
            print('{}: {}'.format(AUDIO_DIR, repr(e)))

        return np.array(features).tolist()

    def music_vectorization(self, spark):                     
        track_id = str(int(time.time() + int(self.filename.split('.mp3')[0])))
        vect = self.compute_features(self.path)
        df = pd.DataFrame({"track_id":track_id, "features":[vect]})
        return track_id, df, vect
