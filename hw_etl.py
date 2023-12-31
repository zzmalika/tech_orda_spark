from pyspark.sql.functions import col, coalesce, udf, broadcast
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, LongType
from pyspark.sql import SparkSession

import geohash
import requests
import json 
import zipfile
import os

zip_folder = 'data/weather/'
destination_folder = 'data/weather/all/'


def get_coordinates(name, country, city):
    query = f"{name}, {country}, {city}"
    url = f'https://api.opencagedata.com/geocode/v1/json?q={query}&key=4ca1952965734b0f94f18afa0e9a7769'
    response = requests.get(url)
    data = response.json()
    if data['results']:
        lat = data['results'][0]['geometry']['lat']
        lng = data['results'][0]['geometry']['lng']
        return lat, lng
    return None, None



def generate_geohash(lat, lng):
    try:
        return geohash.encode(lat, lng, precision=4)
    except Exception as e:
        return None



def unzip_files(zip_folder, destination_folder):
    for filename in os.listdir(zip_folder):
        if filename.endswith('.zip'):
            zip_path = os.path.join(zip_folder, filename)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                listOfFileNames = zip_ref.namelist()
                for elem in listOfFileNames:
                    if elem.startswith('weather/'):
                        zip_ref.extract(elem, destination_folder)             


def main_etl():
    spark = SparkSession.builder \
    .appName("epam_tech_orda_spark") \
    .config("spark.executor.memory", "4g") \
    .config("spark.driver.memory", "4g") \
    .getOrCreate()
    # increased default heap memory

    # EXTRACT ------------------------------------------------------------------
    df = spark.read.csv("data/restaurant_csv/", header=True, inferSchema=True)
    

    # TRANSFORM ------------------------------------------------------------------
    # get only rows with null coordinates
    df_null_coordinates = df.filter(col('lat').isNull() | col('lng').isNull())

    # convert to pandas, to easily iterate
    df_null_coordinates_pandas = df_null_coordinates.toPandas()

    # get coordinates for each place
    # did not use udf, because it is not convinient and optimal
    for index, row in df_null_coordinates_pandas.iterrows():
        lat, lng = get_coordinates(row['franchise_name'], row['country'], row['city'])
        df_null_coordinates_pandas.at[index, 'lat'] = lat
        df_null_coordinates_pandas.at[index, 'lng'] = lng


    updated_coordinates_df = spark.createDataFrame(df_null_coordinates_pandas)

    df_final = df.join(updated_coordinates_df, ['id'], 'left_outer')

    df_final = df_final.select(
        df['id'],
        coalesce(updated_coordinates_df['lat'], df['lat']).alias('lat'),
        coalesce(updated_coordinates_df['lng'], df['lng']).alias('lng'),
        *[df[col] for col in df.columns if col not in ['id', 'lat', 'lng']]
    )
    
    # udf is easy to apply, because there is only 1 output column
    generate_geohash_udf = udf(generate_geohash, StringType())

    df_with_geohash = df_final.withColumn("geohash", generate_geohash_udf("lat", "lng"))
    # unpacking weather dataset in zip files
    unzip_files(zip_folder, destination_folder)

    df_weather = spark.read.format("parquet").load("data/weather/all/")
    df_weather_with_geohash = df_weather.withColumn("geohash", generate_geohash_udf("lat", "lng"))
    # df_weather_with_geohash.count()
    # 112394743

    # we know that lat and lng are same in both datasets, because it was generated by same function
    # thus, we can remove lat and lng from restarants csv, because of left join
    df_with_geohash_cut = df_with_geohash.drop('lat', 'lng')

    # weather dataset has 112394743 rows, whereas restaurant dataset has only 1997 rows
    # weather dataset is much bigger than restaurant dataset, thus we can apply broadcast
    df_joined = df_weather_with_geohash.join(broadcast(df_with_geohash_cut), "geohash", "left")
    # after join, number of rows is 112884619, it is more than original weather dataset
    # it is because geohash len is only 4, thus some coordinates might have same geohash
    # because of duplicates, some coordinates have multiple restaurants and weather info

    # make repartition to optimize performance and memory usage 
    df_repartitioned = df_joined.repartition("year", "month")
    
    # LOAD ------------------------------------------------------------------
    df_repartitioned.write.partitionBy("year", "month").parquet('refined/weather_and_restaurants')
