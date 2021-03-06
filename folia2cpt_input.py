import os
import gzip
from lxml import etree
import logging
import codecs
from fuzzywuzzy import process, fuzz
import argparse
import glob
import datetime
import pandas as pd


logger = logging.getLogger(__name__)
logging.basicConfig(format='%(levelname)s : %(message)s', level=logging.INFO)
logger.setLevel(logging.DEBUG)


NUMBER = 100


class Perspective():
    def __init__(self, name):
        self.name = name
        self.topic_words = []
        self.opinion_words = []

    def __str__(self):
        return 'Perspective: {} - {} topic words; {} opinion words'.format(
            self.name, len(self.topic_words), len(self.opinion_words))

    def write2file(self, out_dir, file_name):
        # create dir (if not exists)
        directory = os.path.join(out_dir, self.name)
        if not os.path.exists(directory):
            os.makedirs(directory)

        # write words to file
        out_file = os.path.join(directory, file_name)
        logger.debug('Writing file {} for perspective {}'.format(out_file, self.name))
        with codecs.open(out_file, 'wb', 'utf8') as f:
            f.write(u'{}\n'.format(' '.join(self.topic_words)))
            f.write(u'{}\n'.format(' '.join(self.opinion_words)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('dir_in', help='directory containing the data '
                        '(gzipped FoLiA XML files)')
    parser.add_argument('dir_out', help='the name of the dir where the '
                        'CPT corpus should be saved.')
    args = parser.parse_args()

    coalitions = pd.read_csv('data/dutch_coalitions.csv', header=None,
                             names=['Date', '1', '2', '3', '4'],
                             index_col=0, parse_dates=True)
    coalitions.sort_index(inplace=True)
    print coalitions
    i = coalitions.index.searchsorted('2010-02-28')
    print 'i:', i
    print coalitions.ix[coalitions.index[i]]

    dir_in = args.dir_in
    dir_out = args.dir_out

    if not os.path.exists(dir_out):
        os.makedirs(dir_out)

    word_tag = '{http://ilk.uvt.nl/FoLiA}w'
    pos_tag = '{http://ilk.uvt.nl/FoLiA}pos'
    t_tag = '{http://ilk.uvt.nl/FoLiA}t'
    lemma_tag = '{http://ilk.uvt.nl/FoLiA}lemma'
    speech_tag = '{http://www.politicalmashup.nl}speech'
    party_tag = '{http://www.politicalmashup.nl}party'
    date_tag = '{http://purl.org/dc/elements/1.1/}date'

    pos_topic_words = ['N']
    pos_opinion_words = ['WW', 'ADJ', 'BW']

    known_parties = ['CDA', 'D66', 'GPV', 'GroenLinks', 'OSF', 'PvdA', 'RPF',
                     'SGP', 'SP', 'VVD', '50PLUS', 'AVP', 'ChristenUnie',
                     'Leefbaar Nederland', 'LPF', 'PvdD', 'PVV']

    data_files = glob.glob('{}/*/data_folia/*.xml.gz'.format(dir_in))

    for i, data_file in enumerate(data_files):
        logger.debug('{} ({} of {})'.format(data_file, i+1, len(data_files)))
        if i % NUMBER == 0:
            logger.info('{} ({} of {})'.format(data_file, i+1,
                                               len(data_files)))

        f = gzip.open(data_file)
        context = etree.iterparse(f, events=('end',),
                                  tag=(speech_tag, date_tag),
                                  huge_tree=True)

        data = {}
        for party in known_parties:
            data[party] = Perspective(party)
        num_speech = 0
        num_speech_without_party = 0

        # Government vs. opposition
        go_data = {}
        go_data['g'] = Perspective('Government')
        go_data['o'] = Perspective('Opposition')

        for event, elem in context:
            if elem.tag == date_tag:
                d = datetime.datetime.strptime(elem.text, "%Y-%m-%d").date()
                i = coalitions.index.searchsorted(d)
                c = coalitions.ix[coalitions.index[i-1]].tolist()
                coalition_parties = [p for p in c if str(p) != 'nan']
            if elem.tag == speech_tag:
                num_speech += 1
                party = elem.attrib.get(party_tag)
                if party:
                    # prevent unwanted subdirectories to be created (happens
                    # when there is a / in the party name)
                    party = party.replace('/', '-')

                    if not data.get(party):
                        p, score1 = process.extractOne(party, known_parties)
                        score2 = fuzz.ratio(party, p)
                        logger.debug('Found match for "{}" to known party "{}" (scores: {}, {})'.format(party, p, score1, score2))
                        if score1 >= 90 and score2 >= 90:
                            # change party to known party
                            logger.debug('Change party "{}" to known party "{}"'.format(party, p))
                            party = p
                            if not data.get(party):
                                data[party] = Perspective(party)
                        else:
                            # add new Perspective
                            logger.debug('Add new perspective for party "{}"'.format(party))
                            data[party] = Perspective(party)

                    if party in coalition_parties:
                        go_perspective = 'g'
                    else:
                        go_perspective = 'o'
                    logger.debug('date: {}, party: {}, government or opposition: {}'.format(str(d), party, go_perspective))

                    # find all words
                    word_elems = elem.findall('.//{}'.format(word_tag))
                    for w in word_elems:
                        pos = w.find(pos_tag).attrib.get('class')
                        l = w.find(lemma_tag).attrib.get('class')
                        if pos in pos_topic_words:
                            data[party].topic_words.append(l)
                            go_data[go_perspective].topic_words.append(l)
                        if pos in pos_opinion_words:
                            data[party].opinion_words.append(l)
                            go_data[go_perspective].opinion_words.append(l)
                else:
                    num_speech_without_party += 1
        del context
        f.close()

        logger.debug('{}: # speech: {} - # speech without party: {}'.format(data_file, num_speech, num_speech_without_party))
        if i % NUMBER == 0:
            for p, persp in data.iteritems():
                logger.info('{}: {}'.format(data_file, persp))

        # write data to file
        min_words = 100
        for p, persp in data.iteritems():
            if len(persp.topic_words) >= min_words and \
               len(persp.opinion_words) >= min_words:
                fpath, fname = os.path.split(data_file)
                persp.write2file('{}/parties'.format(dir_out),
                                 '{}.txt'.format(fname))
        for p, persp in go_data.iteritems():
            if len(persp.topic_words) >= min_words and \
               len(persp.opinion_words) >= min_words:
                fpath, fname = os.path.split(data_file)
                persp.write2file('{}/gov_opp'.format(dir_out),
                                 '{}.txt'.format(fname))
