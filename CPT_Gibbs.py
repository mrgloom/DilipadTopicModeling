"""Do CPT using Gibbs sampling.

Uses Gensim fucntionality.

Papers:
- Finding scientific topics
- A Theoretical and Practical Implementation Tutorial on Topic Modeling and
Gibbs Sampling
- Mining contrastive opinions on political texts using cross-perspective topic
model
"""

from __future__ import division
import numpy as np
import CPTCorpus
import glob
import logging
import time
import pandas as pd
import os

from gibbs_inner import gibbs_inner


logger = logging.getLogger(__name__)
logging.basicConfig(format='%(levelname)s : %(message)s', level=logging.INFO)


class GibbsSampler():
    def __init__(self, corpus, nTopics=10, alpha=0.02, beta=0.02, beta_o=0.02,
                 nIter=2, out_dir=None):
        self.corpus = corpus
        self.nTopics = nTopics
        self.alpha = alpha
        self.beta = beta
        self.nPerspectives = len(self.corpus.perspectives)
        self.beta_o = beta_o
        self.nIter = nIter

        self.out_dir = out_dir
        if self.out_dir:
            if not os.path.exists(self.out_dir):
                os.makedirs(out_dir)

        #self._initialize()

    def _initialize(self):
        """Initializes the Gibbs sampler."""
        self.VT = len(self.corpus.topicDictionary)
        self.VO = len(self.corpus.opinionDictionary)
        self.DT = len(self.corpus)
        self.DO = np.array([len(p.opinionCorpus)
                            for p in self.corpus.perspectives], dtype=np.int)
        self.maxDocLengthT = max([p.topicCorpus.maxDocLength
                                 for p in self.corpus.perspectives])
        self.maxDocLengthO = np.array([p.opinionCorpus.maxDocLength
                                       for p in self.corpus.perspectives],
                                      dtype=np.int)

        # topics
        self.z = np.zeros((self.DT, self.maxDocLengthT), dtype=np.int)
        self.ndk = np.zeros((self.DT, self.nTopics), dtype=np.int)
        self.nkw = np.zeros((self.nTopics, self.VT), dtype=np.int)
        self.nk = np.zeros(self.nTopics, dtype=np.int)
        self.ntd = np.zeros(self.DT, dtype=np.float)

        # opinions
        self.x = np.array([np.zeros((self.DO[i], self.maxDocLengthO[i]),
                                    dtype=np.int)
                           for i, p in enumerate(self.corpus.perspectives)])
        self.nrs = np.zeros((self.nPerspectives, self.nTopics, self.VO),
                            dtype=np.int)
        self.ns = np.zeros((self.nPerspectives, self.nTopics), dtype=np.int)

        # loop over the words in the corpus
        for d, persp, d_p, doc in self.corpus:
            for w_id, i in self.corpus.words_in_document(doc, 'topic'):
                topic = np.random.randint(0, self.nTopics)
                self.z[d, i] = topic
                self.ndk[d, topic] += 1
                self.nkw[topic, w_id] += 1
                self.nk[topic] += 1
                self.ntd[d] += 1

            for w_id, i in self.corpus.words_in_document(doc, 'opinion'):
                opinion = np.random.randint(0, self.nTopics)
                self.x[persp][d_p, i] = opinion
                self.nrs[persp, opinion, w_id] += 1
                self.ns[persp, opinion] += 1
        logger.debug('Finished initialization.')

    def p_z(self, d, w_id):
        """Calculate (normalized) probabilities for p(w|z) (topics).

        The probabilities are normalized, because that makes it easier to
        sample from them.
        """
        f1 = (self.ndk[d]+self.alpha) / \
             (np.sum(self.ndk[d])+self.nTopics*self.alpha)
        f2 = (self.nkw[:, w_id]+self.beta) / \
             (self.nk+self.beta*self.VT)

        p = f1*f2
        return p / np.sum(p)

    def p_x(self, persp, d, w_id):
        """Calculate (normalized) probabilities for p(w|x) (opinions).

        The probabilities are normalized, because that makes it easier to
        sample from them.
        """
        f1 = (self.nrs[persp, :, w_id]+self.beta_o) / \
             (self.ns[persp]+self.beta_o*self.VO)
        # The paper says f2 = nsd (the number of times topic s occurs in
        # document d) / Ntd (the number of topic words in document d).
        # 's' is used to refer to opinions. However, f2 makes more sense as the
        # fraction of topic words assigned to a topic.
        # Also in test runs of the Gibbs sampler, the topics and opinions might
        # have different indexes when the number of opinion words per document
        # is used instead of the number of topic words.
        f2 = self.ndk[d]/self.ntd[d]

        p = f1*f2
        return p / np.sum(p)

    def sample_from(self, p):
        """Sample (new) topic from multinomial distribution p.
        Returns a word's the topic index based on p_z.

        The searchsorted method is used instead of
        np.random.multinomial(1,p).argmax(), because despite normalizing the
        probabilities, sometimes the sum of the probabilities > 1.0, which
        causes the multinomial method to crash. This probably has to do with
        machine precision.
        """
        return np.searchsorted(np.cumsum(p), np.random.rand())

    def theta_topic(self):
        """Calculate theta based on the current word/topic assignments.
        """
        f1 = self.ndk+self.alpha
        f2 = np.sum(self.ndk, axis=1, keepdims=True)+self.nTopics*self.alpha
        return f1/f2

    def phi_topic(self):
        """Calculate phi based on the current word/topic assignments.
        """
        f1 = self.nkw+self.beta
        f2 = np.sum(self.nkw, axis=1, keepdims=True)+self.VT*self.beta
        return f1/f2

    def phi_opinion(self, persp):
        """Calculate phi based on the current word/topic assignments.
        """
        f1 = self.nrs[persp]+float(self.beta_o)
        f2 = np.sum(self.nrs[persp], axis=1, keepdims=True)+self.VO*self.beta_o
        return f1/f2

    def run(self):
        if not self.out_dir:
            # store all parameter samples in memory
            theta_topic = np.zeros((self.nIter, self.DT, self.nTopics))
            phi_topic = np.zeros((self.nIter, self.nTopics, self.VT))

            phi_opinion = [np.zeros((self.nIter, self.nTopics, self.VO))
                           for p in self.corpus.perspectives]
        else:
            # create directories where parameter samples are stored
            self.parameter_dir = '{}/parameter_samples'.format(self.out_dir)
            if not os.path.exists(self.parameter_dir):
                os.makedirs(self.parameter_dir)

        for t in range(self.nIter):
            t1 = time.clock()
            logger.debug('Iteration {} of {}'.format(t+1, self.nIter))

            gibbs_inner(self)

            # calculate theta and phi
            if not self.out_dir:
                theta_topic[t] = self.theta_topic()
                phi_topic[t] = self.phi_topic()

                for p in range(self.nPerspectives):
                    phi_opinion[p][t] = self.phi_opinion(p)
            else:
                pd.DataFrame(self.theta_topic()).to_csv('{}/theta_{:04d}.csv'.format(self.parameter_dir, t))
                pd.DataFrame(self.phi_topic()).to_csv('{}/phi_topic_{:04d}.csv'.format(self.parameter_dir, t))
                for p in range(self.nPerspectives):
                    pd.DataFrame(self.phi_opinion(p)).to_csv('{}/phi_opinion_{}_{:04d}.csv'.format(self.parameter_dir, p, t))

            t2 = time.clock()
            logger.debug('time elapsed: {}'.format(t2-t1))

        if not self.out_dir:
            # calculate means of parameters in memory
            phi_topic = np.mean(phi_topic, axis=0)
            theta_topic = np.mean(theta_topic, axis=0)
            for p in range(self.nPerspectives):
                phi_opinion[p] = np.mean(phi_opinion[p], axis=0)
        else:
            # load numbers from files
            theta_topic = self.load_parameters('theta')
            phi_topic = self.load_parameters('phi_topic')
            phi_opinion = {}
            for p in range(self.nPerspectives):
                phi_opinion[p] = self.load_parameters('phi_opinion_{}'.format(p))

        self.topics = self.to_df(phi_topic, self.corpus.topicDictionary,
                                 self.VT)
        self.opinions = [self.to_df(phi_opinion[p],
                                    self.corpus.opinionDictionary,
                                    self.VO)
                         for p in range(self.nPerspectives)]
        self.document_topic_matrix = self.to_df(theta_topic)

    def load_parameters(self, name):
        data = None
        for i in range(self.nIter):
            fName = '{}/{}_{:04d}.csv'.format(self.parameter_dir, name, i)
            ar = pd.read_csv(fName, index_col=0).as_matrix()
            if data is None:
                data = np.array([ar])
            else:
                data = np.append(data, np.array([ar]), axis=0)
        return np.mean(data, axis=0)

    def print_topics_and_opinions(self, top=10):
        """Print topics and associated opinions.

        The <top> top words and weights are printed.
        """
        for i in range(self.nTopics):
            print u'Topic {}: {}'. \
                  format(i, self.print_topic(self.topics.loc[:, i].copy(),
                                             top))
            print
            for p in range(self.nPerspectives):
                print u'Opinion {}: {}'. \
                      format(self.corpus.perspectives[p].name,
                             self.print_topic(self.opinions[p].loc[:, i].copy(),
                                              top))
            print '-----'
            print

    def print_topic(self, series, top=10):
        """Prints the top 10 words in the topic/opinion on a single line."""
        series.sort(ascending=False)
        t = [u'{} ({:.4f})'.format(word, p)
             for word, p in series[0:top].iteritems()]
        return u' - '.join(t)

    def to_df(self, data, dictionary=None, vocabulary=None):
        if dictionary and vocabulary:
            # phi (topics and opinions)
            words = [dictionary.get(i) for i in range(vocabulary)]
            df = pd.DataFrame(data, columns=words)
            df = df.transpose()
        else:
            # theta (topic document matrix)
            df = pd.DataFrame(data)
        return df

    def topics_and_opinions_to_csv(self):
        # TODO: fix case when self.topics and/or self.opinions do not exist

        if self.out_dir:
            path = self.out_dir
        else:
            path = ''

        self.topics.to_csv(os.path.join(path, 'topics.csv'), encoding='utf8')
        self.document_topic_matrix.to_csv(os.path.join(path,
                                                       'document-topic.csv'))
        for p in range(self.nPerspectives):
            p_name = self.corpus.perspectives[p].name
            f_name = 'opinions_{}.csv'.format(p_name)
            self.opinions[p].to_csv(os.path.join(path, f_name),
                                    encoding='utf8')


if __name__ == '__main__':
    logger.setLevel(logging.DEBUG)

    files = glob.glob('/home/jvdzwaan/data/tmp/dilipad/gov_opp/*')
    #files = glob.glob('/home/jvdzwaan/data/dilipad/perspectives/*')

    corpus = CPTCorpus.CPTCorpus(files)
    corpus.filter_dictionaries(minFreq=5, removeTopTF=100, removeTopDF=100)
    sampler = GibbsSampler(corpus, nTopics=100, nIter=2, out_dir='/home/jvdzwaan/data/tmp/dilipad/test_parameters')
    #sampler = GibbsSampler(corpus, nTopics=100, nIter=2)
    sampler._initialize()
    sampler.run()
    #sampler.print_topics_and_opinions()
    sampler.topics_and_opinions_to_csv()
    #sampler.parameter_dir = '/home/jvdzwaan/data/tmp/dilipad/test_parameters/parameter_samples/'
    #theta_topic = sampler.load_parameters('theta')
    #phi_topic = sampler.load_parameters('phi_topic')
    #phi_opinion = {}
    #for p in range(sampler.nPerspectives):
    #        phi_opinion[p] = sampler.load_parameters('phi_opinion_{}'.format(p))
    #print theta_topic
    #print phi_topic
   # print phi_opinion
