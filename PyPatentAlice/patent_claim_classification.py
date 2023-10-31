# -*- coding: utf-8 -*-
"""
Author: Dominik Jurek

DATE: 10/27/2023
METHOD: Classsify issued patent claims in relevant USPC classes and CPC groups
        using the model trained in 'NLP_model_building.py'
"""

#######################################################
#   Load Environment
#######################################################

import pandas as pd
import numpy as np
import re
import os
import shutil

import pickle
import joblib

import requests
from io import BytesIO
import zipfile
import csv

import multiprocessing as mp

from lime.lime_text import LimeTextExplainer
import wordcloud
from sklearn.feature_extraction.text import TfidfVectorizer

#----------------------------------------
# Expand field limit to iterate through all claims
import ctypes
csv.field_size_limit(int(ctypes.c_ulong(-1).value // 2))
csv.field_size_limit()


# Set seed
RANDOM_SEED = 42


#=========================================================================
#   Full text classification of patent claims
#=========================================================================
def patent_claim_classification_PatentView(year: int,
                                           affected_patent_list: list, 
                                           nlp_model,
                                           output_path: str):
    '''
    METHOD: Extracts from PatentsView claim text and predict classification 
                for issued patents.
    INPUT:  year (int): issue year for claim text.
            affected_patent_list (list): list with relevant 'patent_id' as int.
            nlp_model: sklearn-pipeline for claim classification.
            output_path (str): output directory.
    OUTPUT/RETURN: 
            'FullText_patentClaim_predicted_{year}' (pd.DataFrame): 
                Extracted independent claims text for issue year
                and predicted classification from the nlp model; 
                saved also in 'output_path'
             'PredOnly_patentClaim_predicted_{year}' (pd.DataFrame): 
                Shortened frame with patent_id and predicted classification; 
                saved also in 'output_path'
    '''

    #------------------------------
    # Turn patent id list into in
    affected_patent_list = [int(i) for i in affected_patent_list if not(np.isnan(i))]

    #------------------------------
    print('\t Search treated patent claims for year ' + str(year), flush=True)
    try:
        # Load the fulltext from the patent claism

        patent_claim_master_url = 'https://s3.amazonaws.com/data.patentsview.org/claims/g_claims_{0}.tsv.zip'

        url_link_list = patent_claim_master_url.format(year)

        # Wrap around limited amount of retrys
        for request_attempt in range(5):
            r = requests.get(url_link_list)
            # Check if no error and length is correct
            if (r.ok == True) & \
               (len(r.content) == int(r.headers['Content-Length'])):
               break

        z = zipfile.ZipFile(BytesIO(r.content))
        #z = zipfile.ZipFile(r"C:\Users\domin\Downloads\claims_2016.tsv.zip")

        patent_claims = pd.read_csv(z.open(z.infolist()[0].filename), delimiter="\t", 
                                    quoting=csv.QUOTE_NONNUMERIC, low_memory=False)

        
        #------------------------------------------
        # Limit observations to independent claims that are in the patent DF
        indep = (patent_claims.dependent.isnull())|(patent_claims.dependent==-1)|\
            (patent_claims.dependent=='-1')
        indep_patent_claims = patent_claims[indep]

        #------------------------------------------
        # Cleaning the entries and remove digits from the beginning
        indep_patent_claims.loc[:, 'claim_text'] = indep_patent_claims.claim_text.astype(str).apply(
            lambda x: re.sub(r'^\d{1,3}\.{0,1}\s', '', x).strip())

        #------------------------------------------
        # Further control for independent claims following https://www.uspto.gov/sites/default/files/documents/patent_claims_methodology.pdf
        # And check via reg expression if there is a reference to a different claim
        indep_patent_claims.loc[:, 'dep_reference'] = indep_patent_claims.claim_text.apply(
            lambda x: bool(re.search(r'\bclaim\s+\d+\b|\bclaims\s+\d+\b', str(x))))
        indep_patent_claims = indep_patent_claims[~indep_patent_claims.dep_reference]

        #------------------------------------------
        # Select patents which are in the searched classes
        # Note that I focus on utility patents, which have as identifier an integer
        # See: https://www.uspto.gov/patents-application-process/applying-online/patent-number#:~:text=A%20Patent%20Number%20is%20assigned,six%2C%20seven%20or%20eight%20digits.
        indep_patent_claims['patent_id'] = pd.to_numeric(indep_patent_claims.patent_id,
                                                         downcast = 'integer', errors = 'coerce')
        indep_patent_claims = indep_patent_claims[~indep_patent_claims.patent_id.isnull()]

        searched_indep_patents = indep_patent_claims[indep_patent_claims.patent_id.isin(affected_patent_list)]
        searched_indep_patents.reset_index(inplace=True, drop=True)

        #------------------------------------------
        # Predicted invalidaiton likelihood
        predicted_porbabilities_text = nlp_model.predict_proba(searched_indep_patents.claim_text)
        predictions_df = pd.DataFrame(predicted_porbabilities_text, 
                                      columns=nlp_model.classes_.astype(str))

        predicted_label_text = pd.DataFrame(nlp_model.predict(searched_indep_patents.claim_text),
                                            columns=['predicted_label'])

        predictions_df = pd.concat([predictions_df,
                                    predicted_label_text],
                                   axis=1,
                                   ignore_index=True,
                                   sort=False,
                                   verify_integrity=False)

        #-----------------------------------------
        # Merge with text data
        independent_claim_probabilities = pd.concat([searched_indep_patents,
                                                     predictions_df],
                                                    axis=1,
                                                    ignore_index=True,
                                                    sort=False,
                                                    verify_integrity=False)

        # Rename data frame columns
        name_list = list(searched_indep_patents.columns)
        name_list.extend(nlp_model.classes_.astype(str))
        name_list.extend(['predicted_label'])
        independent_claim_probabilities.columns = name_list

        #----------------------------------------
        # Output Result
        independent_claim_probabilities.to_csv(
            path_or_buf = output_path + '/FullText_patentClaim_predicted_' 
            + str(year) + '.csv', index=False, encoding = 'utf-8')
        print('\t Lenght of output DF of classified independent claims for year ' + str(year) 
              + ': ' + str(len(independent_claim_probabilities)), flush=True)

        independent_claim_probabilities[
            ['patent_id', 'claim_sequence', '0', '1', 'predicted_label']].\
            to_csv(path_or_buf = output_path + '/PredOnly_patentClaim_predicted_' 
                   + str(year) + '.csv', index=False, encoding = 'utf-8')

        return([independent_claim_probabilities, 
                independent_claim_probabilities[
                    ['patent_id', 'claim_sequence', '0', '1', 'predicted_label']]])

    except Exception as exc:
        print('\t Error in claim search for year: ' + str(year) + ' => ' + str(exc))

        return([pd.DataFrame(), pd.DataFrame()])


#=============================================
# Classification Testing with Lime
#=============================================
def lime_text_explainer(patent_classification: pd.DataFrame,
                        model,
                        output_path: str,
                        version_string: str = '',
                        size: int = 1000):
    r'''
    METHOD: Use LIME explainer to find words most relevant for the classification
            as valid or invalid
    INPUT: patent_classification (pd.DataFrame): classified patents text as attribute 'claim_text'.
           model: sklearn-pipeline used for classification.  Need to have a 'predict_proba' method.
           output_path (str): directory to save outputs.
           version_string (str): added to saved output files.
           size (int): size of sample drawn.
    OUTPUT: top_words_LIMETextExplainer_raw (pd.DataFrame): 
                containing the estimated 'weights' for the classification as invalid
                for respective 'word' in the claim from each sample; 
                saved also in 'output_path'
            top_words_LIMETextExplainer_aggregated (pd.DataFrame):
                'count' how many times a 'word' is found 
                 relevant for the classification in the sample texts and
                 and 'mean' of assigned weight; 
                saved also in 'output_path'
    r'''
    #----------------------------------
    # Visualization with LIME
    # Source: https://towardsdatascience.com/explain-nlp-models-with-lime-shap-5c5a9f84d59b
    #         https://marcotcr.github.io/lime/tutorials/Lime%20-%20multiclass.html
    #         https://marcotcr.github.io/lime/tutorials/Lime%20-%20basic%20usage%2C%20two%20class%20case.html
    #         https://medium.com/@ageitgey/natural-language-processing-is-fun-part-3-explaining-model-predictions-486d8616813c
    #         https://www.oreilly.com/content/introduction-to-local-interpretable-model-agnostic-explanations-lime/

    # Define Lime text explainer for a sample string
    explainer = LimeTextExplainer(random_state=RANDOM_SEED)

    # Iterate over 'size' strings random sample and show the most frequent word
    lime_text_sample = patent_classification.sample(n=size, random_state=RANDOM_SEED)

    #------------------------------------
    # Iterate through sample
    top_label_df = pd.DataFrame(columns=['word', 'weight'])
    for text_string in lime_text_sample.claim_text:
        try:
            exp = explainer.explain_instance(text_instance=str(text_string),
                                             classifier_fn=model.predict_proba)

            top_label_df = pd.concat([top_label_df,
                                      pd.DataFrame(exp.as_list(), columns=['word', 'weight'])],
                                      axis = 0, ignore_index = True)
        except Exception as ex:
            print('\t\t Error in Lime Text exlanation: Following text casued issue: ' 
                  + str(text_string) + '=>' + str(ex))

    #------------------------------------
    # Output
    top_label_df.sort_values('weight', ascending=[False])
    top_label_df.to_csv(
        path_or_buf = output_path + '/top_words_LIMETextExplainer_raw_'
        +str(version_string)+'.csv',
        index=False, encoding='utf-8')

    #------------------------------------
    # Grouping and output
    top_label_df_grouped = top_label_df.groupby(['word'])['weight'].agg(
        ['mean', 'count']).reset_index()
    top_label_df_grouped.to_csv(
        path_or_buf = output_path 
        + '/top_words_LIMETextExplainer_aggregated_'
        +str(version_string)+'.csv',
        index=False, encoding='utf-8')

    return(top_label_df, top_label_df_grouped)

#====================================================
# Helper function for word cloud visualization
#====================================================
def wordcloud_creation(model_data, 
                       output_directory,
                       version_string=''):
    '''create word clouds for classified claim text data 
        (with columns 'claim_text' and 'treated') in output_directory'''
    #=========================================
    # Create word cloud of word
    # Documentation: https://amueller.github.io/word_cloud/generated/wordcloud.WordCloud.html#
    wc = wordcloud.WordCloud(stopwords=wordcloud.STOPWORDS,
                             background_color='white',
                             max_font_size=40,
                             color_func=lambda *args, **kwargs: "black",
                             random_state=RANDOM_SEED)

    #-----------------------------------------
    # Define treated and uncreated corpa
    treated_texts = [str(t) for t in  model_data.loc[model_data.treated==1, 'claim_text']]
    treated_text = ' '.join(treated_texts)

    untreated_texts = [str(t) for t in  model_data.loc[model_data.treated==0, 'claim_text']]
    untreated_text = ' '.join(untreated_texts)

    #-----------------------------------------
    # Generate word clouds and save
    treated_wc = wc.generate(treated_text)
    treated_wc.to_file(
        output_directory + '/wc_unweighted_predicted_treated_patentClaims_'
        +str(version_string)+'.jpg')

    untreated_wc = wc.generate(untreated_text)
    untreated_wc.to_file(
        output_directory + '/wc_unweighted_predicted_untreated_patentClaims_'
        +str(version_string)+'.jpg')


    #===========================================
    # Create word cloud weighted for differential frequency of words in treated and non-treated corpa

    #-----------------------------------------
    # Weight by differential frequency of terms in trated and non-treated corpa
    vectorized_model = TfidfVectorizer(use_idf=False,
                                       smooth_idf=False,
                                       stop_words=list(wordcloud.STOPWORDS))
    vectorized_corpa = vectorized_model.fit_transform([treated_text, untreated_text])

    #-----------------------------------------
    # Find relative difference between vectors (already normalized to unit length)
    differential_frequency = vectorized_corpa.toarray()[0] - vectorized_corpa.toarray()[1]

    #-----------------------------------------
    # create arrays for vector differences
    untreated_feature_frequency = []
    for f in differential_frequency:
        if f < 0:
            untreated_feature_frequency.append(-f)
        else:
            untreated_feature_frequency.append(0)
    # normalize to unit norm
    norm = np.linalg.norm(untreated_feature_frequency)
    untreated_feature_frequency = untreated_feature_frequency/norm

    # Get normalized vector for term difference of treated features
    treated_feature_frequency = []
    for f in differential_frequency:
        if f > 0:
            treated_feature_frequency.append(f)
        else:
            treated_feature_frequency.append(0)
    norm = np.linalg.norm(treated_feature_frequency)
    treated_feature_frequency = treated_feature_frequency/norm

    #-----------------------------------------
    # get terms from tfidf vector model and pair with weights
    wc_untreated_weights = {}
    for item in list(zip(vectorized_model.get_feature_names_out(), 
                         untreated_feature_frequency)):
        wc_untreated_weights[item[0]] = item[1]

    wc_treated_weights = {}
    for item in list(zip(vectorized_model.get_feature_names_out(), 
                         treated_feature_frequency)):
        wc_treated_weights[item[0]] = item[1]

    #-----------------------------------------
    # Create word cloud for both
    diff_treated_wc = wc.generate_from_frequencies(wc_treated_weights)
    diff_treated_wc.to_file(
        output_directory 
        + '/wc_differential_frequency_weighting_predicted_treated_claims_'
        +str(version_string)+'.jpg')

    diff_untreated_wc = wc.generate_from_frequencies(wc_untreated_weights)
    diff_untreated_wc.to_file(
        output_directory 
        + '/wc_differential_frequency_weighting_predicted_untreated_claims_'
        +str(version_string)+'.jpg')

    return

#============================================================
# Helper function word word cloud for LIME important word
#============================================================
def wordcloud_top_label_df(top_label_df_grouped,
                           output_directory,
                           version_string=''):
    '''Word cloud creation for top words found in LIME for treatment and control'''
    top_treated_words = top_label_df_grouped[top_label_df_grouped['mean'] > 0]. \
        sort_values(['count'], ascending=False).head(200)
    top_untreated_words = top_label_df_grouped[top_label_df_grouped['mean'] < 0]. \
        sort_values(['count'], ascending=False).head(200)

    #-----------------------------------------
    # get terms from LIME model and pair with frequency count
    wc_untreated_weights = {}
    for item in list(zip(top_untreated_words['word'], top_untreated_words['count'])):
        wc_untreated_weights[item[0]] = item[1]

    wc_treated_weights = {}
    for item in list(zip(top_treated_words['word'], top_treated_words['count'])):
        wc_treated_weights[item[0]] = item[1]


    #------------------------------------------
    wc = wordcloud.WordCloud(stopwords=wordcloud.STOPWORDS,
                                 background_color='white',
                                 max_font_size=40,
                                 color_func=lambda *args, **kwargs: "black",
                                 random_state=RANDOM_SEED)

    #-----------------------------------------
    # Create word cloud for both
    treated_wc = wc.generate_from_frequencies(wc_treated_weights)
    treated_wc.to_file(
        output_directory + '/wc_lime_top_words_treated_claims_'
        +str(version_string)+'.jpg')

    untreated_wc = wc.generate_from_frequencies(wc_untreated_weights)
    untreated_wc.to_file(
        output_directory + '/wc_lime_top_words_untreated_claims_'
        +str(version_string)+'.jpg')

    return

##############################################################
# Main Routine
##############################################################
if __name__ == '__main__':
    
    #------------------------------------
    # Set parameters for execution as main
    home_directory = os.getcwd()
    os.chdir(home_directory)

    # Note, 'issued_patents_control' version using patent claims for controls always use 
    # non-resampling in the training dataset construction, while 
    # 'ApplicationControls' version always use resampling (smaller number of eligible controls)
    
    #----------------------------------------
    # Define how many cpc classes should be considered for classification 
    # (for general classification and only focusing on USPC class 705)
    CPC_NCLASSES = 5

    CPC_NCLASSES_705 = 3

    # For testing purposes, focus on the main configuration using 
    # published patent claims as control data.  I include in comments 
    # additional configuration that may be used, they all refer
    # to different training set constructions
    input_version='issued_patents_control'
    output_version='issued_patents_control'
    cpc_nclasses=CPC_NCLASSES

    r'''
    input_version='ApplicationControls'
    output_version='ApplicationControls'
    cpc_nclasses=CPC_NCLASSES

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Only load 705 USPC class as mainclass
    input_version='issued_patents_control_only705'
    output_version='issued_patents_control_only705'
    cpc_nclasses=CPC_NCLASSES_705
    
    input_version='ApplicationControls_only705'
    output_version='ApplicationControls_only705'
    cpc_nclasses=CPC_NCLASSES_705
    r'''

    # Define years to classify -> for testing, define two years before and after Alice.
    MIN_CLASSIFICATION_YEAR = 2012 
    MAX_CLASSIFICATION_YEAR = 2016

    #---------------------------------------------------
    # Define PatentsView directory
    PatentsView_directory = 'PatentsView_raw_data'

    # Define directory for other data that are not PatentsView
    USPTO_data_directory = 'USPTO_raw_data'
    if not os.path.exists(USPTO_data_directory):
        os.makedirs(USPTO_data_directory)

    print('Start Main Routine', flush=True)

    import time
    from datetime import timedelta
    start_time = time.time()
    
    #====================================
    # Define execution environment
    #====================================

    output_directory = r'patent_classification'

    # Create Output Path if not already exist
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)


    #-----------------------------------
    # Output director for word cloud (should already exist from NLP model build)
    wc_output_directory = 'Wordcloud_'+str(output_version)

    # Create WC Output Path if not already exist
    if not os.path.exists(wc_output_directory):
        os.makedirs(wc_output_directory)

    #-----------------------------------
    # Output director for LIME data frames and word clouds
    lime_output_directory = 'LIME_'+str(output_version)

    # Create WC Output Path if not already exist
    if not os.path.exists(lime_output_directory):
        os.makedirs(lime_output_directory)


    # Load model
    text_poly2_svc = joblib.load('TFIDF_SVC_'+str(input_version) 
                                 + '//tfidf_svc_poly2_' + str(input_version) + '.joblib')


    #===============================================
    #   Collect searchable patents
    #===============================================
    print('\t Find patents that fit the desired classes', flush=True)

    #-------------------------------
    # uspc_current classifications
    #-------------------------------
    # Use current uspc classification to be more in aligne with cpc classification, AND
    # there seems to be an issue with classifications only reaching until 2013 assignments
    if ('uspc_current_PatentsView.tsv' in os.listdir(PatentsView_directory)):
        #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # Local
        uspc_current = pd.read_csv(PatentsView_directory + '/uspc_current_PatentsView.tsv', 
                                   delimiter="\t", quoting=csv.QUOTE_NONNUMERIC, low_memory=False)
    else:
        #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # Load application data from Patent View

        # Wrap around limited amount of retrys
        for request_attempt in range(5):
            r = requests.get(r"https://s3.amazonaws.com/data.patentsview.org/download/uspc_current.tsv.zip")
            if (r.ok == True) & \
               (len(r.content) == int(r.headers['Content-Length'])):
               break
        z = zipfile.ZipFile(BytesIO(r.content))
        z.infolist()[0].filename = 'uspc_current_PatentsView.tsv'
        z.extract(z.infolist()[0])

        uspc_current = pd.read_csv(z.open(z.infolist()[0]), delimiter="\t", 
                                   quoting=csv.QUOTE_NONNUMERIC, low_memory=False)

        shutil.move('uspc_current_PatentsView.tsv', 
                    PatentsView_directory + '/uspc_current_PatentsView.tsv')

    #---------------------------------
    # Load main uspc classes
    with open('main_classes_' + str(input_version) + '.pkl', 'rb') as fp:
         uspc_main_category = pickle.load(fp)

    print('\t USPC Main Classes for classification: ' + str(uspc_main_category), flush=True)

    #==============================================
    # Find patents to investigate in affected USPC classes

    # => Note that the loaded categories are in int
    uspc_main_category_str = [str(c) for c in uspc_main_category]

    uspc_current['mainclass_id'] = uspc_current['mainclass_id'].astype(str).\
        apply(lambda s: re.sub(r'^0*', '', str(s).split('.')[0]))

    uspc_affected_patent = uspc_current[uspc_current.mainclass_id.isin(uspc_main_category_str)]
    uspc_affected_patent = uspc_affected_patent[['patent_id', 'mainclass_id']].drop_duplicates()
    uspc_affected_patent['patent_id'] = pd.to_numeric(uspc_affected_patent.patent_id,
                                                      downcast='integer', errors='coerce')

    uspc_affected_patent_list = list(set(uspc_affected_patent.patent_id))

    print('\t Number of identified patents to be classified from USPC classes: '
          + str(len(uspc_affected_patent_list)), flush=True)

    # =============================================================================
    #  Extend USPC affected patents list with application data
    # =============================================================================
    # Append here the application data which contain a lot of uspc class information 
    # to identify relevant applications for classifcation

    print('\t Expand with patent ids from application data', flush=True)
    #----------------------------------
    # Application Data
    #----------------------------------
    if ('application_data_2020.csv' in os.listdir(USPTO_data_directory)):
        #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # Local
        application_data = pd.read_csv(USPTO_data_directory
                                       + '/application_data_2020.csv', low_memory=False)
    r'''
    # Note: the server connection is slow and frequently times out, it 
    # is advised to instead load the file manually from the bulk data website
    # Source: https://bulkdata.uspto.gov/data/patent/pair/economics/2020
    # File: application_data.csv.zip
    # Donwload, extract into home directory, and rename to 'application_data_2020.csv'
    else:
        #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # Load application data from economic research dataset

        # Wrap around limited amount of retrys
        for request_attempt in range(5):
            r = requests.get(r"https://bulkdata.uspto.gov/data/patent/pair/economics/2020/application_data.csv.zip",
                             timeout=10, stream=True) #-> the file is quite large and the server is slow
            if (r.ok == True) & \
               (len(r.content) == int(r.headers['Content-Length'])):
               break

        z = zipfile.ZipFile(BytesIO(r.content))
        z.infolist()[0].filename = 'application_data_2020.csv'
        z.extract(z.infolist()[0])

        application_data = pd.read_csv(z.open(z.infolist()[0]), low_memory=False)

        shutil.move('application_data_2020.csv', 
            USPTO_data_directory + '/application_data_2020.csv')

    r'''


    # Select the application with the fitting patent classes
    application_data['uspc_class'] = application_data['uspc_class'].astype(str).\
        apply(lambda s: re.sub(r'^0*', '', str(s).split('.')[0]))

    affected_applications = application_data[application_data.uspc_class.isin(uspc_main_category_str)]

    # Coerce to integer, since focusing on utility patents
    affected_applications['patent_id'] = pd.to_numeric(affected_applications.patent_number,
                                                       downcast='integer', errors='coerce')
    uspc_affected_application_publication_list = list(set(affected_applications.patent_id))

    # Expand patent list by patent ids from application data
    uspc_affected_patent_list.extend(uspc_affected_application_publication_list)

    print('\t Number of identified patents to be classified from USPC classes including application ids: '
          + str(len(uspc_affected_patent_list)), flush=True)

    # =============================================================================
    # CPC classification
    # =============================================================================
    # Note: most patents are now classified via CPC, as in Dugan (2018)
    # Translate affected patents into CPC classes
    # See: https://www.uspto.gov/patents-application-process/patent-search/classification-standards-and-development
    #-------------------------------
    # cpc classifications
    #-------------------------------
    if ('cpc_current_PatentsView.tsv' in os.listdir(PatentsView_directory)):
        #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # Local
        cpc_current = pd.read_csv(PatentsView_directory + '/cpc_current_PatentsView.tsv', 
                                  delimiter="\t", quoting=csv.QUOTE_NONNUMERIC, low_memory=False)
    else:
        #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # Load application data from Patent View

        # Wrap around limited amount of retrys
        for request_attempt in range(5):
            r = requests.get(r"https://s3.amazonaws.com/data.patentsview.org/download/g_cpc_current.tsv.zip")
            if (r.ok == True) & \
               (len(r.content) == int(r.headers['Content-Length'])):
               break

        z = zipfile.ZipFile(BytesIO(r.content))
        z.infolist()[0].filename = 'cpc_current_PatentsView.tsv'
        z.extract(z.infolist()[0])

        cpc_current = pd.read_csv(z.open(z.infolist()[0]), delimiter="\t", 
                                  quoting=csv.QUOTE_NONNUMERIC, low_memory=False)

        shutil.move('cpc_current_PatentsView.tsv', 
            PatentsView_directory + '/cpc_current_PatentsView.tsv')

    #-------------------------------------
    # Focus on primary categories
    cpc_current = cpc_current[cpc_current.cpc_type=='inventional']

    # Drop unneeded columns and make cpc groups unique
    cpc_current = cpc_current.drop(['cpc_section',
                                    'cpc_type',
                                    'cpc_class', 
                                    'cpc_group',
                                    'cpc_sequence'], axis=1).drop_duplicates().\
        rename(columns={'cpc_subclass':'group_id'})

    # Cast id to int
    cpc_current['patent_id'] = pd.to_numeric(cpc_current.patent_id,
                                             downcast='integer', errors='coerce')

    #-------------------------------------
    # Find patent_ids in the CPC data for the affected USPC classes
    cpc_uspc_patents = cpc_current[cpc_current.patent_id.isin(
        [s for s in uspc_affected_patent_list if not(np.isnan(s))])]

    # select most common cpc classes
    main_cpc_classes = list(set(cpc_uspc_patents.group_id.value_counts(). \
                                nlargest(cpc_nclasses).reset_index()['index']))

    print('\t Most frequent CPC classes\n' 
          + str(cpc_uspc_patents.group_id.value_counts(normalize=True).head(10).cumsum()), flush=True)
    #G06Q    => DATA PROCESSING SYSTEMS OR METHODS, SPECIALLY ADAPTED FOR ADMINISTRATIVE, COMMERCIAL, FINANCIAL, MANAGERIAL, SUPERVISORY OR FORECASTING PURPOSES; SYSTEMS OR METHODS SPECIALLY ADAPTED FOR ADMINISTRATIVE, COMMERCIAL, FINANCIAL, MANAGERIAL, SUPERVISORY OR FORECASTING PURPOSES, NOT OTHERWISE PROVIDED FOR
    #G06F    => ELECTRIC DIGITAL DATA PROCESSING
    #G07F    => COIN-FREED OR LIKE APPARATUS
    #H04L    => TRANSMISSION OF DIGITAL INFORMATION, e.g. TELEGRAPHIC COMMUNICATION
    #A63F    => CARD, BOARD, OR ROULETTE GAMES; INDOOR GAMES USING SMALL MOVING PLAYING BODIES; VIDEO GAMES; GAMES NOT OTHERWISE PROVIDED FOR
    #H04N    => PICTORIAL COMMUNICATION, e.g. TELEVISION
    #G09B    => EDUCATIONAL OR DEMONSTRATION APPLIANCES; APPLIANCES FOR TEACHING, OR COMMUNICATING WITH, THE BLIND, DEAF OR MUTE; MODELS; PLANETARIA; GLOBES; MAPS; DIAGRAMS
    #G16H    => HEALTHCARE INFORMATICS, i.e. INFORMATION AND COMMUNICATION TECHNOLOGY [ICT] SPECIALLY ADAPTED FOR THE HANDLING OR PROCESSING OF MEDICAL OR HEALTHCARE DATA
    #A61B    => DIAGNOSIS; SURGERY; IDENTIFICATION
    #G01N    => INVESTIGATING OR ANALYSING MATERIALS BY DETERMINING THEIR CHEMICAL OR PHYSICAL PROPERTIES


    print('\t Selected main CPC classes\n' + str(main_cpc_classes), flush=True)

    # Define as patent ids those patents within the respective main CPC classes
    cpc_affected_patent_list = list(cpc_current[cpc_current.group_id.isin(main_cpc_classes)]['patent_id'])
    print('\t Number of affected cpc based patents: ' + str(len(cpc_affected_patent_list)), flush=True)

    del cpc_current, uspc_current

    #------------------------------------------
    print("\t\tElapsed Execution time: " + str(timedelta(seconds=(time.time() - start_time))), flush=True)

    #=========================================================================
    #   Prediction of Patent Classification from PatentView full text
    #=========================================================================
    print('\tClassification for patents that are in relevant USPC classes', flush=True)

    # Define result storage df
    text_poly2_svc_classification_uspc = pd.DataFrame()

    #-----------------------------------
    # Create sub-director for extracted texts to be classified
    suboutput_dir = output_directory+'//uspcAffected__TFIDF_poly2_'+ str(output_version)
    if not os.path.exists(suboutput_dir):
        os.makedirs(suboutput_dir)

    r'''
    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Parallel Execution -> not recommended for desktop 
    cores = mp.cpu_count()
    print('\t\t Number of Cores: ' + str(cores), flush=True)
    
    pool = mp.Pool(cores)
    # Run the scraping method for the contents required
    for year in range(MIN_CLASSIFICATION_YEAR, MAX_CLASSIFICATION_YEAR+1):
        print('\t\tAffected patents in uspc classes for issue year: ' + str(year) + '\n')
        pool.apply_async(
                        patent_claim_classification_PatentView,
                        args=(
                              year,
                              uspc_affected_patent_list,
                              text_poly2_svc,
                              suboutput_dir
                              )
                        )
    pool.close()
    pool.join()
    r'''
    
    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Linear Execution
    for year in range(MIN_CLASSIFICATION_YEAR, MAX_CLASSIFICATION_YEAR+1):
        print('\t\tAffected patents in uspc classes for issue year: ' + str(year) + '\n')
        patent_claim_classification_PatentView(year=year,
                                               affected_patent_list=uspc_affected_patent_list,
                                               nlp_model=text_poly2_svc,
                                               output_path=suboutput_dir)

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Load from Target
    patent_classification_files = os.listdir(suboutput_dir)
    patent_classification_path = [suboutput_dir+'//'+f for f in patent_classification_files if \
                                  bool(re.search('.csv', f)) & bool(re.search('FullText', f)) & \
                                      bool(re.search('patentClaim', f))  & \
                                          bool(re.search(r'\d{4,4}', f))]

    for load_file in patent_classification_path:
        append_df = pd.read_csv(load_file, encoding='utf-8', low_memory=False)
        append_df['year'] = re.search(r'\d{4,4}', load_file).group(0)

        text_poly2_svc_classification_uspc = pd.concat(
            [text_poly2_svc_classification_uspc,
             append_df],
            axis=0)

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Save Fulltext output
    text_poly2_svc_classification_uspc.to_csv(
        path_or_buf = output_directory +
        '/FullText__patents_uspcAffected__predicted__TFIDF_poly2_' 
        + str(output_version) + '.csv', index=False, encoding = 'utf-8')

    print('\t\t Total length of classified patent claims - affected USPC based; TFIDF + SVC Poly 2: ' +
          str(len(text_poly2_svc_classification_uspc)), flush=True)
    print('\t\t Unique classified patents - affeced USPC based; SVC Poly 2: ' +
          str(len(text_poly2_svc_classification_uspc.patent_id.unique())), flush=True)

    #------------------------------------------
    print("\t\tElapsed Execution time: " + str(timedelta(seconds=(time.time() - start_time))), flush=True)

    #--------------------------------------
    # Repeat process for patent identified using CPC groups
    print('\tClassification for patents that are in relevant CPC groups', flush=True)
 
    # Define result storage df
    text_poly2_svc_classification_cpc = pd.DataFrame()

    #-----------------------------------
    # Create sub-director for extracted texts to be classified
    suboutput_dir = output_directory+'//cpcAffected__TFIDF_poly2_'+ str(output_version)
    if not os.path.exists(suboutput_dir):
        os.makedirs(suboutput_dir)

    r'''
    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Parallel Execution -> not recommended for desktop 
    cores = mp.cpu_count()
    print('\t\t Number of Cores: ' + str(cores), flush=True)

    pool = mp.Pool(cores)
    # Run the scraping method for the contents required
    for year in range(MIN_CLASSIFICATION_YEAR, MAX_CLASSIFICATION_YEAR+1):
        print('\t\tAffected patents in cpc group for issue year: ' + str(year) + '\n')
        pool.apply_async(
                        patent_claim_classification_PatentView,
                        args=(
                              year,
                              cpc_affected_patent_list,
                              text_poly2_svc,
                              suboutput_dir
                              )
                        )
    pool.close()
    pool.join()
    r'''

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Linear Execution
    for year in range(MIN_CLASSIFICATION_YEAR, MAX_CLASSIFICATION_YEAR+1):
        print('\t\tAffected patents in cpc group for issue year: ' + str(year) + '\n')
        patent_claim_classification_PatentView(year,
                                               cpc_affected_patent_list,
                                               text_poly2_svc,
                                               suboutput_dir)

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Load from Target
    patent_classification_files = os.listdir(suboutput_dir)
    patent_classification_path = [suboutput_dir+'//'+f for f in patent_classification_files if \
                                  bool(re.search('.csv', f)) & bool(re.search('FullText', f)) & \
                                    bool(re.search('patentClaim', f)) & \
                                          bool(re.search(r'\d{4,4}', f))]

    for load_file in patent_classification_path:
        append_df = pd.read_csv(load_file, encoding='utf-8', low_memory=False)
        append_df['year'] = re.search(r'\d{4,4}', load_file).group(0)

        text_poly2_svc_classification_cpc = pd.concat(
            [text_poly2_svc_classification_cpc,
             append_df],
            axis=0)

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # Save Fulltext output
    text_poly2_svc_classification_cpc.to_csv(
        path_or_buf = output_directory +
        '/FullText__patents_cpcAffected__predicted__TFIDF_poly2_' 
        + str(output_version) + '.csv', index=False, encoding = 'utf-8')

    print('\t\t Total length of classified patent claims - affected CPC based; TFIDF + SVC Poly 2: ' +
          str(len(text_poly2_svc_classification_cpc)), flush=True)
    print('\t\t Unique classified patents - affected CPC based; SVC Poly 2: ' +
          str(len(text_poly2_svc_classification_cpc.patent_id.unique())), flush=True)


    #------------------------------------------
    print("\t\tElapsed Execution time: " + str(timedelta(seconds=(time.time() - start_time))), flush=True)


    #=================================================
    # WordCloud Generation
    #=================================================
    print('\t Create Word Clouds for predicted patent classes', flush=True)

    model_data_text_poly2_svc_uspc = text_poly2_svc_classification_uspc[
        ['claim_text', '1', 'predicted_label']
        ].drop_duplicates()

    # Create model text for word cloud and predict classification
    model_data_text_poly2_svc_uspc = model_data_text_poly2_svc_uspc.rename(
        columns={'1':'pred_treated'})

    model_data_text_poly2_svc_uspc['treated'] = (model_data_text_poly2_svc_uspc.\
                                                 predicted_label == 1).astype(int)

    wordcloud_creation(model_data=model_data_text_poly2_svc_uspc,
                       output_directory=wc_output_directory,
                       version_string='patents_uspcAffected__TFIDF_poly2_' 
                       + str(output_version))

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    model_data_text_poly2_svc_cpc = text_poly2_svc_classification_cpc[
        ['claim_text', '1', 'predicted_label']
        ].drop_duplicates()

    # Create model text for word cloud and predict classification
    model_data_text_poly2_svc_cpc = model_data_text_poly2_svc_cpc.rename(columns={'1':'pred_treated'})

    model_data_text_poly2_svc_cpc['treated'] = (model_data_text_poly2_svc_cpc.predicted_label == 1).\
        astype(int)

    # There are vastly more cpc classifications than uspc classification => randomly draw! if size over 1M
    if len(model_data_text_poly2_svc_cpc) > 1000000:
        model_data_text_poly2_svc_cpc = model_data_text_poly2_svc_cpc.sample(
            n=1000000,
            random_state = RANDOM_SEED,
            replace=False
            )

    wordcloud_creation(model_data=model_data_text_poly2_svc_cpc,
                       output_directory=wc_output_directory,
                       version_string='patents_cpcAffected__TFIDF_poly2_'
                       + str(output_version))


    #===============================================================
    # Patent Claim Classification Example
    #===============================================================
    print('\t Lime classification sample analysis', flush=True)

    _, top_label_df_grouped_poly2_svc_uspc =  lime_text_explainer(
        patent_classification=text_poly2_svc_classification_uspc[
            ['claim_text', '1', 'predicted_label']
            ].drop_duplicates(),
        model=text_poly2_svc,
        output_path=lime_output_directory,
        version_string='patents_uspcAffected__TFIDF_poly2_' + str(output_version),
        size=1000)

    wordcloud_top_label_df(top_label_df_grouped=top_label_df_grouped_poly2_svc_uspc,
                           output_directory=lime_output_directory,
                           version_string='__uspcAffected__TFIDF_poly2_' 
                           + str(output_version))

    #^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    _, top_label_df_grouped_poly2_svc_cpc =  lime_text_explainer(
        patent_classification=text_poly2_svc_classification_cpc[
            ['claim_text', '1', 'predicted_label']
            ].drop_duplicates(),
        model=text_poly2_svc,
        output_path=lime_output_directory,
        version_string='patents_cpcAffected__TFIDF_poly2_' + str(output_version),
        size=1000)

    wordcloud_top_label_df(top_label_df_grouped=top_label_df_grouped_poly2_svc_cpc,
                           output_directory=lime_output_directory,
                           version_string='__cpcAffected__TFIDF_poly2_'
                           + str(output_version))


    print("\t\tElapsed Execution time: " + str(timedelta(seconds=(time.time() - start_time))), flush=True)


    print('End Main Routine for version: ' + str(output_version), flush=True)

    