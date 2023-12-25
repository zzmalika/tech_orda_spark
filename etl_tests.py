from hw_etl import get_coordinates

import pytest

def test_1():
    name = 'Savoria'
    country = 'US'
    city = 'Dillon'
    lat, lng = get_coordinates(name, country, city)
    
    expected_coordinates = (39.63026, -106.04335)
    assert expected_coordinates == (lat, lng)