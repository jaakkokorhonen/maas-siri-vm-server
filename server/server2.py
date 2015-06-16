import sys
import BaseHTTPServer
import SimpleHTTPServer

import SocketServer
import time
import datetime
import cgi
import urlparse
import json
import re
import copy
import threading
import unicodecsv

import itertools

import urllib
import urllib2
from xml.etree import ElementTree as ET

from math import radians, cos, sin, asin, sqrt
from os import curdir, sep

#from random import randint

import psycopg2 as db
import ppygis

from FeatureServer.Server import Server as FeatureServer
from FeatureServer.DataSource.PostGIS import PostGIS as FeatureServerPostGIS

class BadRequestException(Exception):
		def __init__(self, value):
				self.value = value
		def __str__(self):
				return self.value

class MaaS():

		def cursor(self):
				if not hasattr(self, 'connection'):
						self.connection=db.connect("dbname=sujuvuusnavigaattori")
				if not hasattr(self, '_cursor'):
						self._cursor=self.connection.cursor()
				return self._cursor

		def wfs(self):
				if not hasattr(self, '_wfs'):
						datasource=FeatureServerPostGIS('maas', dsn='dbname=sujuvuusnavigaattori', layer='report', fid="report_id", geometry='geometry', attribute_cols='speed')
						self._wfs=FeatureServer({'maas': datasource})
				return self._wfs

		def __exit__(self, type, value, traceback):
				self.connection.close()

		def savePlan(self, plan):
				cursor = self.cursor()
				if not 'journey_id' in plan or plan['journey_id'] is None or plan['journey_id'] == '':
						raise BadRequestException('plan journey_id is missing')
				if not 'timestamp' in plan or plan['timestamp'] is None or plan['timestamp'] == '':
						raise BadRequestException('plan timestamp is missing')
				if not 'coordinates' in plan or type(plan['coordinates']) is not list:
						raise BadRequestException('plan coordinates are missing')
				linestring=[]
				for point in plan['coordinates']:
						# as per http://geojson.org/geojson-spec.html
						longitude=float(point[0])
						latitude=float(point[1])
						if len(point) > 2:
								altitude=float(point[2])
						else:
								altitude=0
						linestring.append(ppygis.Point(longitude, latitude, altitude, srid=4326))
				cursor.execute("INSERT INTO plan (geometry, journey_id, timestamp) VALUES (%s, %s, %s) RETURNING plan_id",
													 (ppygis.LineString(linestring, srid=4326), plan['journey_id'], plan['timestamp']))
				plan_id = cursor.fetchone()[0]
				self.connection.commit()
				return plan_id

		def saveTraces(self, traces):
				if type(traces) is not list:
						traces = [traces]
				cursor = self.cursor()
				for trace in traces:
						if not 'journey_id' in trace or trace['journey_id'] is None or trace['journey_id'] == '':
								raise BadRequestException('trace journey_id is missing')
						if not 'timestamp' in trace or trace['timestamp'] is None or trace['timestamp'] == '':
								raise BadRequestException('trace timestamp is missing')
						if not 'latitude' in trace or trace['latitude'] is None or trace['latitude'] == '':
								raise BadRequestException('trace latitude is missing')
						if not 'longitude' in trace or trace['longitude'] is None or trace['longitude'] == '':
								raise BadRequestException('trace longitude is missing')
						if not 'altitude' in trace or trace['altitude'] is None or trace['altitude'] == '':
								trace['altitude']=0
						cursor.execute("INSERT INTO trace (geometry, journey_id, timestamp) VALUES (%s, %s, %s)",
													 (ppygis.Point(float(trace['longitude']), float(trace['latitude']), float(trace['altitude']), srid=4326), trace['journey_id'], trace['timestamp']))
				self.connection.commit()
				return

		def saveRoutes(self, routes):
				if type(routes) is not list:
						routes = [routes]
				cursor = self.cursor()
				for route in routes:
						if not 'journey_id' in route or route['journey_id'] is None or route['journey_id'] == '':
								raise BadRequestException('route journey_id is missing')

						if not 'timestamp' in route or route['timestamp'] is None or route['timestamp'] == '':
								raise BadRequestException('route timestamp is missing')

						if not 'coordinates' in route or type(route['coordinates']) is not list or len(route['coordinates'])!=2 or type(route['coordinates'][0]) is not list or type(route['coordinates'][1]) is not list:
								raise BadRequestException('route point coordinates are incorrect')

						if not 'mode' in route or route['mode'] is None or route['mode'] == '':
								raise BadRequestException('route mode is missing')

						if not 'speed' in route or route['speed'] is None or route['speed'] == '':
								raise BadRequestException('route speed is missing')

						sql = """INSERT INTO route (geometry, journey_id, timestamp, speed, mode, realtime)
										 VALUES (""" + "ST_GeomFromText('LINESTRING(%.10f %.10f %.10f, %.10f %.10f %.10f)', 4326)" % (float(route['coordinates'][0][0]),
																																																									float(route['coordinates'][0][1]),
																																																									float(0 if (not 2 in route['coordinates'][0]) else route['coordinates'][0][2]),
																																																									float(route['coordinates'][1][0]),
																																																									float(route['coordinates'][1][1]),
																																																									float(0 if (not 2 in route['coordinates'][1]) else route['coordinates'][1][2])
																																																								) + ", %s, %s, %s, %s, %s)"
						cursor.execute(sql, (route['journey_id'],
																 route['timestamp'],
																 float(route['speed']),
																 "" if (not 'mode' in route or route['mode'] is None or route['mode'] == '') else route['mode'],
																 False if (not 'realtime' in route or route['realtime'] is None or route['realtime'] == '') else True
																 ))
						self.connection.commit()
				return

		def getPlan(self, plan_id=''):
				if not plan_id:
						raise BadRequestException('plan_id cannot be empty')
				cursor = self.cursor()
				sql = """select plan_id,
												ST_AsGeoJSON(geometry) as geometry,
												journey_id,
												timestamp
												from plan where plan_id = %s"""
				cursor.execute(sql, (plan_id,))
				plan = cursor.fetchone()
				result={}
				if plan is not None:
						result['plan_id']=plan[0]
						result['geometry']=json.loads(plan[1])
						result['journey_id']=plan[2]
						result['timestamp']=plan[3].isoformat()
						plan=result
				return plan

		def getAverageSpeedsReport(self, boundary='', type='', planID='', after='', before=''):

				if type!='baseline':
						if type!='realtime':
								type='combined'

				cursor = self.cursor()

				params=[type]

				sql = "SELECT ST_AsGeoJSON(geometry), speed, reading FROM report WHERE type=%s"

				if len(boundary):
						sql+=" AND (geometry && ST_Envelope('LINESTRING(%s %s 0, %s %s 0)'::geometry))"
						params.append(float(boundary[0][0]))
						params.append(float(boundary[0][1]))
						params.append(float(boundary[1][0]))
						params.append(float(boundary[1][1]))

				if planID:
						sql+=" AND (geometry && (SELECT geometry FROM plan WHERE plan_id=%s))"
						params.append(planID)

				if before or after:
						if before:
								sql+=" AND (timestamp < %s)"
								params.append(before)
						if after:
								sql+=" AND (timestamp > %s)"
								params.append(after)
				else:
						sql+=" AND (timestamp >= (SELECT MAX(timestamp) FROM route) AND timestamp=(SELECT MAX(timestamp) FROM report))"

				sql+=" ORDER BY timestamp DESC"

				cursor.execute(sql, tuple(params))
				records = cursor.fetchall()

				if not before and not after and not len(records):
						self.buildAverageSpeedsReport(type)
						cursor.execute(sql, tuple(params))
						records = cursor.fetchall()

				collection={
						"type": "FeatureCollection",
						"features": []
				}
				for record in records:
						collection['features'].append({
								'type': 'Feature',
								'geometry': json.loads(record[0]),
								'properties': {
										'speed': float(record[1]),
										'reading': float(record[2])
								}
						})
				return collection

		def buildAverageSpeedsReport(self, type='realtime'):
				if type!='baseline':
						if type!='realtime':
								type='combined'

				min_length=250 # shorter non-slow segments will be grouped together

				cursor = self.cursor()

				speeds = {}
				readings = {}

				# Clean up

				sql = "DELETE FROM report"
				if type == 'realtime':
						sql += " WHERE realtime=true"
				elif type == 'baseline':
						sql += " WHERE realtime=false"
				cursor.execute(sql)

				# Build average speed cache for routes

				sql = "SELECT ST_GeoHash(geometry, 30), AVG(speed), COUNT(*) FROM route"
				if type == 'realtime':
						sql += " WHERE  realtime=true"
				elif type == 'baseline':
						sql += " WHERE realtime=false"
				sql += " GROUP BY geometry HAVING COUNT(*) > 0"

				cursor.execute(sql)

				for record in cursor.fetchall():
						speeds[record[0]]=float(record[1])
						readings[record[0]]=float(record[2])

				# Group raw routes into linestring geometries

				sql = "SELECT ST_AsGeoJSON((ST_Dump(ST_LineMerge(ST_Collect(route.geometry)))).geom)::json->'coordinates' FROM (SELECT geometry FROM route GROUP BY geometry HAVING COUNT(*) > 0) AS route"

				cursor.execute(sql)
				collection=[]
				for record in cursor.fetchall():
						linestring=json.loads(record[0])
						cursor.execute("select ST_Length(ST_Transform(ST_GeomFromGeoJSON(%s)::geometry, 2839))",  (json.dumps({'type': 'LineString', 'coordinates': linestring, 'crs': {'type':'name', 'properties': {'name': 'EPSG:4326'}}}),))
						length=cursor.fetchone()[0]
						feature={
								"coordinates": [],
								"speed": 0,
								"reading": 0,
								"remaining-length": length,
								"speeds": [],
								"readings": [],
								"lengths": []
						}
						# run through each group to detect speed category change and exclude short routes
						for i, point in enumerate(linestring):
								# start bulding route segments upon reaching second linestring point
								if len(feature['coordinates'])>0:
										route=[]
										route.append(ppygis.Point(linestring[i-1][0], linestring[i-1][1], linestring[i-1][2], srid=4326))
										route.append(ppygis.Point(linestring[i][0], linestring[i][1], linestring[i][2], srid=4326))
										route=ppygis.LineString(route, srid=4326)
										cursor.execute("select ST_Length(ST_Transform(%s::geometry, 2839)), ST_GeoHash(%s::geometry, 30)",  (route, route))
										record=cursor.fetchone()
										length=record[0]
										if record[1] in speeds:
												speed=speeds[record[1]]
												reading=readings[record[1]]
										else:
												print "Route cache miss", record[1]
												speed=-1
												reading=0

										if len(feature['speeds']):
												if self.categorizeBusSpeed(speed) != self.categorizeBusSpeed(feature['speeds'][-1]):
														if (sum(feature['lengths']) >= min_length and feature['remaining-length'] - sum(feature['lengths'])>=min_length) or (self.categorizeBusSpeed(speed)==1):
																feature['speed']=float(sum(feature['speeds']))/len(feature['speeds'])
																feature['reading']=float(sum(feature['readings']))/len(feature['readings'])
																collection.append([copy.deepcopy(feature['coordinates']), feature['speed'], feature['reading']])
																# reset
																feature['remaining-length']-=sum(feature['lengths'])
																feature['coordinates']=[feature['coordinates'][-1]]
																feature['speed']=0
																feature['reading']=0
																feature['lengths']=[]
																feature['speeds']=[]
																feature['readings']=[]

										feature['coordinates'].append([point[0], point[1], point[2]])

										feature['speeds'].append(speed)
										feature['readings'].append(reading)
										feature['lengths'].append(length)

								else:
										feature['coordinates'].append([point[0], point[1], point[2]])

						feature['speed']=float(sum(feature['speeds']))/len(feature['speeds'])
						feature['reading']=float(sum(feature['readings']))/len(feature['readings'])
						collection.append([copy.deepcopy(feature['coordinates']), feature['speed'], feature['reading']])

				now=datetime.datetime.fromtimestamp(time.time())
				for i, feature in enumerate(collection):
						cursor.execute("INSERT INTO report (geometry, speed, reading, type, timestamp) VALUES (ST_GeomFromGeoJSON(%s), %s, %s, %s, %s)",
													 (json.dumps({'type': 'LineString', 'coordinates': feature[0], 'crs': {'type':'name', 'properties': {'name': 'EPSG:4326'}}}), feature[1], feature[2], type, now))
						collection[i][0]=json.dumps({'type': 'LineString', 'coordinates': feature[0]}) # just as the database would have returned it
						self.connection.commit()

				return collection

		def wfsGetCapabilities(self, base_url):
				data=self.wfs().dispatchRequest(path_info="/maas", params={'service': 'WFS', 'request': 'getCapabilities', 'format': 'wfs'}, base_path=base_url, request_method="GET")
				#print repr(data.getData())
				return data[1]

		def wfsDescribeFeatureType(self, base_url):
				data=self.wfs().dispatchRequest(path_info="/maas", params={'service': 'WFS', 'request': 'describeFeatureType', 'format': 'wfs'}, base_path=base_url, request_method="GET")
				return data[1]

		def wfsGetFeature(self, base_url):
				data=self.wfs().dispatchRequest(path_info="/maas", params={'service': 'WFS', 'request': 'GetFeature', 'format': 'wfs'}, base_path=base_url, request_method="GET")
				return data.getData()

		def categorizeBusSpeed(self, speed):
				speed=speed*3.6
				if speed<=0:
						return 0
				if speed<=15:
						return 1
				if speed<=30:
						return 2
				if speed<=50:
						return 3
				if speed<=80:
						return 4
				if speed<=200:
						return 5
				else:
						return 6
				return -1

		def importGTFSData(self):

			city="Oulu"
			country="Finland"

			cursor = self.cursor()

			try:
				cursor.execute("INSERT INTO mt_city (name, country) VALUES (%s, %s)",
											 (city, country))

				file=unicodecsv.reader(open('gtfs/agency.txt'), encoding='utf-8')
				file.next()
				for line in file:
					if len(line) >= 4:
						cursor.execute("INSERT INTO mt_agency (agency_id, city_id, name, url, timezone, language, phone) VALUES (%s, (SELECT city_id FROM mt_city WHERE name=%s AND country=%s), %s, %s, %s, %s, %s)",
													 (line[0],
														city,
														country,
														line[1]     if len(line) >= 1 else None,
														line[2]     if len(line) >= 2 else None,
														line[3]     if len(line) >= 3 else None,
														line[4]     if len(line) >= 4 else None,
														line[5]     if len(line) >= 5 else None,
													 ))

				file=unicodecsv.reader(open('gtfs/stops.txt'), encoding='utf-8')
				file.next()
				for line in file:
					if len(line) >= 5:
						cursor.execute("INSERT INTO mt_stop (stop_id, city_id, code, name, description, timezone, geometry) VALUES (%s, (SELECT city_id FROM mt_city WHERE name=%s AND country=%s), %s, %s, %s, %s, ST_GeomFromText('POINT(%s %s 0)', 4326))",
													 (line[0],
														city,
														country,
														line[1],
														line[2],
														line[3],
														line[10]    if len(line) >= 10 else None,
														float(line[5]),
														float(line[4]),
														))
				file=unicodecsv.reader(open('gtfs/shapes.txt'), encoding='utf-8')
				file.next()
				shapes={}
				for line in file:
					if len(line) >= 3:
						if line[0] not in shapes:
							shapes[line[0]]={}
						shapes[line[0]][int(line[3])+1]=[float(line[2]), float(line[1]), 0]

				# sort according to sequence number
				for shape_id in shapes:
					shape=[]
					for i in sorted(shapes[shape_id].iterkeys()):
						shape.append(shapes[shape_id][i])
					shapes[shape_id]=shape


				file=unicodecsv.reader(open('gtfs/trips.txt'), encoding='utf-8')
				file.next()
				routes={}
				for line in file:
					if len(line) >= 6:
						if line[0] not in routes:
							routes[line[0]]={'shapes': {}}
						if line[6] not in routes[line[0]]['shapes']:
							if line[6] in shapes:
								routes[line[0]]['shapes'][line[6]]=shapes[line[6]]

				file=unicodecsv.reader(open('gtfs/routes.txt'), encoding='utf-8')
				file.next()
				for line in file:
					if len(line) >= 5:
						if line[0] not in routes:
							routes[line[0]]={'shapes': {}}
						routes[line[0]]['agency_id']=line[1]
						routes[line[0]]['short_name']=line[2]
						routes[line[0]]['full_name']=line[3]
						routes[line[0]]['description']=line[4]
						routes[line[0]]['type']=line[5]
						routes[line[0]]['url']=line[6]

				for route_id in routes:
					route=routes[route_id]
					linestring=[]
					for shape_id in route['shapes']:
						for point in route['shapes'][shape_id]:
							linestring.append(ppygis.Point(point[0], point[1], point[2], srid=4326))
					if len(linestring)>1:
						cursor.execute("INSERT INTO mt_route (route_id, agency_id, short_name, full_name, type, description, url, geometry) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
											 (route_id,
												route['agency_id'],
												route['short_name'],
												route['full_name'],
												int(route['type']),
												route['description'],
												route['url'],
												ppygis.LineString(linestring, srid=4326),
											 ))

				file=unicodecsv.reader(open('gtfs/shapes.txt'), encoding='utf-8')
				file.next()
				shapes={}
				for line in file:
					if len(line) >= 2:
						if line[0] not in shapes:
							shapes[line[0]]=[]
						shapes[line[0]].append([line[2], line[1]])

			except Exception as e:
				print "Error: ", e
				raise

			self.connection.commit()
			return

		def getPublicTransportStops(self):

				cursor = self.cursor()

				sql = """SELECT stop_id,
												ST_AsGeoJSON(geometry) AS geometry,
												name,
												(SELECT EXTRACT(EPOCH FROM (expected_arrival_time - aimed_arrival_time)) FROM mt_stop_visit v1 WHERE s.stop_id = v1.stop_id ORDER BY aimed_arrival_time DESC LIMIT 1) AS delay,
												(SELECT EXTRACT(EPOCH FROM (current_timestamp - MAX(aimed_arrival_time))) FROM mt_stop_visit v2 WHERE s.stop_id = v2.stop_id) AS delay_age
												FROM mt_stop s"""
				cursor.execute(sql)
				records = cursor.fetchall()

				collection={
						"type": "FeatureCollection",
						"features": []
				}
				for record in records:
						collection['features'].append({
								'type': 'Feature',
								'geometry': json.loads(record[1]),
								'properties': {
										'id': record[0],
										'name': record[2],
										'delay': record[3],
										'delay_age': record[4]
								}
						})
				return collection

		def getPublicTransportRoutes(self):

				cursor = self.cursor()

				sql = "select route_id, ST_AsGeoJSON(geometry) as geometry, short_name from mt_route"
				cursor.execute(sql)
				records = cursor.fetchall()

				collection={
						"type": "FeatureCollection",
						"features": []
				}
				for record in records:
						collection['features'].append({
								'type': 'Feature',
								'geometry': json.loads(record[1]),
								'properties': {
										'id': record[0],
										'name': record[2]
								}
						})
				return collection

		def getPublicTransportVehicles(self):
				url='http://92.62.36.215:8080/siri-vm-ws/NotificationProducer'
				headers = {
								"Content-type": "application/xml; charset=utf-8",
								}
				body ="""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:siri="http://wsdl.siri.org.uk/siri" xmlns:siri1="http://www.siri.org.uk/siri">
									 <soapenv:Header/>
									 <soapenv:Body>
											<siri:GetVehicleMonitoring>
												 <ServiceRequestInfo>
														<siri1:RequestTimestamp>2015-06-12T11:50:11.406+02:00</siri1:RequestTimestamp>
														<siri1:RequestorRef>okffi</siri1:RequestorRef>
												 </ServiceRequestInfo>
												 <Request version="2.0">
														<siri1:RequestTimestamp>2015-06-12T11:50:11.406+02:00</siri1:RequestTimestamp>
														<siri1:LineRef>3</siri1:LineRef>
												 </Request>
												 <RequestExtension>
														<!--You may enter ANY elements at this point-->
												 </RequestExtension>
											</siri:GetVehicleMonitoring>
									 </soapenv:Body>
								</soapenv:Envelope>"""

				opener = urllib2.build_opener()
				req = urllib2.Request(url, body, headers)
				res = opener.open(req)
				xml = res.read();

				root = ET.fromstring(xml)
				collection={
						"type": "FeatureCollection",
						"features": []
				}
				for activity in root.findall('.//{http://www.siri.org.uk/siri}VehicleActivity'):
					lng=activity.find('.//{http://www.siri.org.uk/siri}VehicleLocation/{http://www.siri.org.uk/siri}Longitude[1]').text
					lat=activity.find('.//{http://www.siri.org.uk/siri}VehicleLocation/{http://www.siri.org.uk/siri}Latitude[1]').text
					ref=activity.find('.//{http://www.siri.org.uk/siri}VehicleRef').text
					direction=activity.find('.//{http://www.siri.org.uk/siri}DirectionRef').text
					collection['features'].append({
							'type': 'Feature',
							'geometry': {
								'type': 'Point',
								'coordinates': [lng,lat,0]
							},
							'properties': {
								'id': ref,
								'direction': direction
							}
					})
				return collection

		def pullPublicTransportStopVisitTimes(self, offset=0):

				limit=2

				cursor = self.cursor()
				url='http://92.62.36.215:8080/siri/sm?MaximumStopVisits=1&id='
#				url='http://127.0.0.1:8080/siri/sm?MaximumStopVisits=1&id='
				headers = {
								"Content-type": "application/xml; charset=utf-8",
								}
				sql = """SELECT stop_id FROM mt_stop s ORDER BY stop_id ASC LIMIT %s OFFSET %s"""
				cursor.execute(sql, (limit, offset,))
				records = cursor.fetchall()

				opener = urllib2.build_opener()
				for record in records:
					#print "Updating stop " + str(record[0])
					req = urllib2.Request(url + str(record[0]), None, headers)
					res = opener.open(req)
					xml = res.read();

					root = ET.fromstring(xml)
					for activity in root.findall('.//{http://www.siri.org.uk/}MonitoredStopVisit'):
						aimedTime=activity.find('.//{http://www.siri.org.uk/}AimedArrivalTime').text
						expectedTime=activity.find('.//{http://www.siri.org.uk/}ExpectedArrivalTime').text
						try:
							cursor.execute("INSERT INTO mt_stop_visit (stop_id, aimed_arrival_time, expected_arrival_time) VALUES (%s, %s, %s)",
																 (record[0], aimedTime, expectedTime))
						except db.IntegrityError as e:
							self.connection.rollback()
						except:
							raise
						else:
							self.connection.commit()
					#print "done updating stop"

				if(not len(records)):
					offset=0
				else:
					offset+=limit

				threading.Timer(5.0, lambda: MaaS().pullPublicTransportStopVisitTimes(offset)).start()
				return

		def pullPublicTransportVehicleSpeeds(self, offset=0):

				limit=1

				cursor = self.cursor()
				url='http://92.62.36.215:8080/siri-vm-ws/NotificationProducer'
#				url='http://127.0.0.1:8080/siri-vm-ws/NotificationProducer'
				headers = {
								"Content-type": "application/xml; charset=utf-8",
								}

				sql = """SELECT distinct route_id, short_name, ST_AsGeoJSON(geometry) FROM mt_route r ORDER BY route_id ASC LIMIT %s OFFSET %s"""
				cursor.execute(sql, (limit, offset,))
				records = cursor.fetchall()

				opener = urllib2.build_opener()


				for record in records:
					#print "Updating line " + str(record[1])
					body ="""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:siri="http://wsdl.siri.org.uk/siri" xmlns:siri1="http://www.siri.org.uk/siri">
										 <soapenv:Header/>
										 <soapenv:Body>
												<siri:GetVehicleMonitoring>
													 <ServiceRequestInfo>
															<siri1:RequestTimestamp>2015-06-12T11:50:11.406+02:00</siri1:RequestTimestamp>
															<siri1:RequestorRef>okffi</siri1:RequestorRef>
													 </ServiceRequestInfo>
													 <Request version="2.0">
															<siri1:RequestTimestamp>2015-06-12T11:50:11.406+02:00</siri1:RequestTimestamp>
															<siri1:LineRef>""" + record[1] + """</siri1:LineRef>
													 </Request>
													 <RequestExtension>
															<!--You may enter ANY elements at this point-->
													 </RequestExtension>
												</siri:GetVehicleMonitoring>
										 </soapenv:Body>
									</soapenv:Envelope>"""

					req = urllib2.Request(url, body, headers)
					res = opener.open(req)
					xml = res.read();

					root = ET.fromstring(xml)

					for activity in root.findall('.//{http://www.siri.org.uk/siri}MonitoredVehicleJourney[{http://www.siri.org.uk/siri}PreviousCalls][{http://www.siri.org.uk/siri}DirectionRef][{http://www.siri.org.uk/siri}LineRef]'):

						vehicleRef=activity.find('.//{http://www.siri.org.uk/siri}VehicleRef')
						lineRef=activity.find('.//{http://www.siri.org.uk/siri}LineRef')
						directionRef=activity.find('.//{http://www.siri.org.uk/siri}DirectionRef')
						journeyStartTime=activity.find('.//{http://www.siri.org.uk/siri}PreviousCalls/{http://www.siri.org.uk/siri}PreviousCall[last()]/{http://www.siri.org.uk/siri}AimedArrivalTime')
						journeyID = "oulubuses.line:" + lineRef.text + ".direction:" + directionRef.text + ".vehicle:" + vehicleRef.text + ".start:" + journeyStartTime.text

						measurements=[]

						stops=activity.findall('.//{http://www.siri.org.uk/siri}PreviousCalls/{http://www.siri.org.uk/siri}PreviousCall[{http://www.siri.org.uk/siri}StopPointRef][{http://www.siri.org.uk/siri}ActualArrivalTime][{http://www.siri.org.uk/siri}ActualDepartureTime]')

						for i, stop in enumerate(stops):
							measurements.append({
								'stopRef': stop.find('{http://www.siri.org.uk/siri}StopPointRef').text,
								'arrivalTime': stop.find('{http://www.siri.org.uk/siri}ActualArrivalTime').text,
								'departureTime': stop.find('{http://www.siri.org.uk/siri}ActualDepartureTime').text
							});
							if i>0:
								sql = """SELECT ST_Length(ST_Transform(geometry, 2839)) as distance,
																EXTRACT(EPOCH FROM (%s::timestamptz - %s::timestamptz)) AS duration,
																ST_AsGeoJSON(geometry) AS geometry
													FROM (SELECT
																		ST_Line_Substring(
																			r.geometry,
																			LEAST(ST_Line_Locate_Point(r.geometry, (SELECT geometry FROM mt_stop WHERE stop_id=%s)),
																					ST_Line_Locate_Point(r.geometry, (SELECT geometry FROM mt_stop WHERE stop_id=%s))),
																			GREATEST(ST_Line_Locate_Point(r.geometry, (SELECT geometry FROM mt_stop WHERE stop_id=%s)),
																					ST_Line_Locate_Point(r.geometry, (SELECT geometry FROM mt_stop WHERE stop_id=%s)))
																		) AS geometry
																FROM mt_route r
																WHERE short_name=%s) s"""
								cursor.execute(sql, (measurements[i-1]['arrivalTime'],
																		 measurements[i]['departureTime'],
																		 measurements[i-1]['stopRef'],
																		 measurements[i]['stopRef'],
																		 measurements[i-1]['stopRef'],
																		 measurements[i]['stopRef'],
																		 record[1]
																		 ,))
								for route in cursor.fetchall():
									distance=route[0]
									duration=route[1]
									linestring=json.loads(route[2])['coordinates']
									for j, point in enumerate(linestring):
										if j>0:
											route=[]
											route.append(ppygis.Point(linestring[j-1][0], linestring[j-1][1], linestring[j-1][2], srid=4326))
											route.append(ppygis.Point(linestring[j][0], linestring[j][1], linestring[j][2], srid=4326))
											route=ppygis.LineString(route, srid=4326)
											try:
												cursor.execute("INSERT INTO route (geometry, journey_id, timestamp, speed, mode, realtime) VALUES (%s, %s, %s, %s, %s, %s)",
																			(route, journeyID, measurements[i]['departureTime'], 0 if duration == 0 else distance/duration, "BUS", False, ))
											except db.IntegrityError as e:
												self.connection.rollback()
											except:
												raise
											else:
												self.connection.commit()
					#print "done updating line"

				if(not len(records)):
					offset=0
				else:
					offset+=limit

				threading.Timer(5.0, lambda: MaaS().pullPublicTransportVehicleSpeeds(offset)).start()
				return

class ServerHandler(SimpleHTTPServer.SimpleHTTPRequestHandler):
		def do_GET(self):
				try:
						parsed_path = urlparse.urlparse(self.path)
						query_components = urlparse.parse_qs(parsed_path.query)
						match=re.compile("/plans/(?P<planID>[0-9]+)").match(parsed_path.path)
						if match and match.group('planID'):
								self.send_response_body(MaaS().getPlan(int(match.group('planID'))))
						elif "/reports/speed-averages" == parsed_path.path:
								boundary=[]
								if 'boundary_sw_lon' in query_components and 'boundary_sw_lat' in query_components and 'boundary_ne_lon' in query_components and 'boundary_ne_lat' in query_components:
												boundary=[[query_components['boundary_sw_lon'][0], query_components['boundary_sw_lat'][0]],[query_components['boundary_ne_lon'][0], query_components['boundary_ne_lat'][0]]]

								self.send_response_body(MaaS().getAverageSpeedsReport(boundary,
																																			query_components['type'][0]     if 'type'     in query_components else None,
																																	int(query_components['planID'][0])  if 'planID'   in query_components else None,
																																			query_components['after'][0]    if 'after'    in query_components else None,
																																			query_components['before'][0]   if 'before'   in query_components else None))
						elif "/demo.html" == parsed_path.path:
								f = open(curdir + sep +'demo.html')
								self.send_response(200)
								self.send_header('Content-type', 'text/html; charset=utf-8')
								self.end_headers()
								self.wfile.write(f.read())
								return
						elif "/siridemo.html" == parsed_path.path:
								f = open(curdir + sep +'siridemo.html')
								self.send_response(200)
								self.send_header('Content-type', 'text/html; charset=utf-8')
								self.end_headers()
								self.wfile.write(f.read())
								return
						elif "/wfs.xml" == parsed_path.path:
								try:
										base_url='http://'+self.headers.getheader('Host')+'/wfs.xml'
										data=None
										if not 'REQUEST' in query_components or query_components['REQUEST'] is None or query_components['REQUEST']=='':
												raise BadRequestException('REQUEST parameter is required')
										if query_components['REQUEST'][0] == 'GetCapabilities':
												data=MaaS().wfsGetCapabilities(base_url)
										elif query_components['REQUEST'][0] == 'describeFeatureType':
												data=MaaS().wfsDescribeFeatureType(base_url)
										elif query_components['REQUEST'][0] == 'GetFeature':
												data=MaaS().wfsGetFeature(base_url)

										if data is None:
												self.send_response(404)
												self.send_header('Content-type', 'text/xml; charset=utf-8')
												self.end_headers()
												self.wfile.write('<error>Not found</error>')
										else:
												self.send_response(200)
												self.send_header('Content-type', 'text/xml; charset=utf-8')
												self.end_headers()
												self.wfile.write(data)
								except BadRequestException as e:
										self.send_response(400)
										self.send_header('Content-type', 'text/xml; charset=utf-8')
										self.end_headers()
										self.wfile.write('<error>' + str(e) + '</error>')
								except Exception as e:
										self.send_response(500)
										self.send_header('Content-type', 'text/xml; charset=utf-8')
										self.end_headers()
										self.wfile.write('<error>' + str(e) + '</error>')
										raise
								except:
										self.send_response(500)
										self.send_header('Content-type', 'text/xml; charset=utf-8')
										self.end_headers()

#						elif "/import/siri/vm" == parsed_path.path:
#								self.send_response_body(MaaS().importSIRIData())
						elif "/gtfs/import" == parsed_path.path:
								self.send_response_body(MaaS().importGTFSData())
						elif "/pt/stops" == parsed_path.path:
								self.send_response_body(MaaS().getPublicTransportStops())
						elif "/pt/routes" == parsed_path.path:
								self.send_response_body(MaaS().getPublicTransportRoutes())
						elif "/pt/vehicles" == parsed_path.path:
								self.send_response_body(MaaS().getPublicTransportVehicles())
						elif "/" == parsed_path.path:
								self.send_response_body({"name": "MaaS API Server (with SIRI)", "documentation": "https://github.com/okffi/maas-siri-vm-server", "version": "1.0"})
						else:
								self.send_error(404, "Not found")
				except BadRequestException as e:
						self.send_error(400, str(e))
				except Exception as e:
						self.send_error(500, str(e))
						raise
				except:
						self.send_error(500)
						raise
				return

		def do_POST(self):
				try:
						parsed_path = urlparse.urlparse(self.path)
						query_components = urlparse.parse_qs(parsed_path.query)
						length = int(self.headers.getheader('content-length'))
						post = self.rfile.read(int(length))
						if self.headers.getheader('Content-type') and self.headers.getheader('Content-type').startswith('application/json'):
								try:
										data = json.loads(unicode(post.decode()))
								except:
										raise BadRequestException('data must be valid JSON')
						else:
								data=urlparse.parse_qs(post.decode())
								if 'payload' not in data:
										raise BadRequestException('must use application/json or payload parameter to submit data')
								try:
										data = json.loads(unicode(data['payload'][0]))
								except:
										raise BadRequestException('data must be valid JSON')
						if "/plans" == parsed_path.path:
								self.send_response_body(MaaS().savePlan(data))
						elif "/traces" == parsed_path.path:
								self.send_response_body(MaaS().saveTraces(data))
						elif "/routes" == parsed_path.path:
								self.send_response_body(MaaS().saveRoutes(data))
						else:
								self.send_error(404, "Not found")
				except BadRequestException as e:
						self.send_error(400, str(e))
				except Exception as e:
						self.send_error(500, str(e))
				except:
						self.send_error(500)
				return

		def do_OPTIONS(self):
				self.send_response(200, 'OK')
				self.send_cors_headers(True)
				self.end_headers()

		def send_cors_headers(self, options=False):
				if options==True:
						self.send_header("Access-Control-Allow-Origin", "*")
						self.send_header("Access-Control-Expose-Headers", "Access-Control-Allow-Origin")
						self.send_header("Access-Control-Allow-Headers", "Origin, X-Requested-With, Accept, Content-type, Accept-Timezone, *")
						self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
				else:
						self.send_header('Content-type', 'application/json; charset=utf-8')
						self.send_header("Access-Control-Allow-Origin", "*")

		def send_response_body(self, body):
				if body is None or body=='':
						self.send_response(204, 'No Content')
						self.send_cors_headers()
						self.end_headers()
				else:
						self.send_response(200, 'OK')
						try:
								data=bytes(unicode(json.dumps(body)))
								self.send_cors_headers()
								self.end_headers()
								self.wfile.write(data)
						except Exception as e:
								self.send_error(500, str(e))
								raise
						except:
								self.send_error(500)
								raise

		def send_error(self, code, message=''):
				if code == 400:
						self.send_response(code, "Bad request")
				elif code == 404:
						self.send_response(code, "Not Found")
				elif code == 500:
						self.send_response(code, "Internal Server Error")
				else:
						self.send_response(code, "Generic error")
				self.send_cors_headers()
				self.end_headers()
				self.wfile.write(bytes(unicode(json.dumps({"error": code, "description": message}))))

class ForkingHTTPServer(SocketServer.ForkingMixIn, BaseHTTPServer.HTTPServer):
		def finish_request(self, request, client_address):
				request.settimeout(600)
				# "super" can not be used because BaseServer is not created from object
				BaseHTTPServer.HTTPServer.finish_request(self, request, client_address)

def httpd(handler_class=ServerHandler, server_address=('localhost', 80)):
		print "Starting automatic tasks..."
		try:
				MaaS().pullPublicTransportStopVisitTimes()
				MaaS().pullPublicTransportVehicleSpeeds()
				print "Starting MaaS API Server..."
				srvr = ForkingHTTPServer(server_address, handler_class)
				srvr.serve_forever()  # serve_forever
		except KeyboardInterrupt:
				srvr.socket.close()

if __name__ == "__main__":
		if sys.argv[1:]:
				port = int(sys.argv[1])
		else:
				port = 80
		server_address = ('0.0.0.0', port)
		httpd(server_address=server_address)
