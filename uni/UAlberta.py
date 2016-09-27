"""
Copyright (c) 2016 Stepan Fedorko-Bartos, Ceegan Hale

Under MIT License - https://github.com/Step7750/ScheduleStorm/blob/master/LICENSE.md

This file is a resource for Schedule Storm - https://github.com/Step7750/ScheduleStorm
"""

import threading
import requests
import pymongo
from bs4 import BeautifulSoup
import time
import logging
from ldap3 import Server, Connection, SUBTREE, ALL, LEVEL

log = logging.getLogger("UAlberta")


class UAlberta(threading.Thread):
    def __init__(self, settings):
        threading.Thread.__init__(self)
        self.settings = settings
        self.db = pymongo.MongoClient().ScheduleStorm

    def getTerms(self):
        """
        API Handler

        Returns the distinct terms in the database, along with their name and id

        :return: **dict** Keys are the ids, values are the proper names
        """
        termlist = self.db.UAlbertaCourseList.distinct("term")
        responsedict = {}
        for term in termlist:
            responsedict[str(term)] = self.db.UAlbertaTerms.distinct(str(term))[0]
        return responsedict

    def getLocations(self):
        """
        API Handler

        Returns a list of all locations at UAlberta

        :return: **list** Contains 1D with the possible locations
        """
        locations = self.db.UAlbertaCourseList.distinct("location")
        response = []

        for location in locations:
            if location != "":
                response.append(location)

        return response

    def retrieveCourseDesc(self, courses):
        """
        Given a course list from an API handler, retrieves course descriptions and sorts by faculty

        Pure Function

        :param courses: **dict** List of courses from API handler
        :return: **dict** Faculty sorted dict with course descriptions
        """
        facultydict = {}

        # Get the descriptions for each subject
        for subject in courses:
            result = self.db.UAlbertaSubjects.find_one({"subject": subject})

            if result:
                del result["_id"]
                del result["subject"]
                del result["lastModified"]

                if "faculty" not in result:
                    result["faculty"] = "Other"

                if result["faculty"] not in facultydict:
                    facultydict[result["faculty"]] = {}

                facultydict[result["faculty"]][subject] = courses[subject]

                facultydict[result["faculty"]][subject]["description"] = result

        return facultydict

    def getSubjectListAll(self, term):
        """
        API Handler

        Returns all data for a given term (classes, descriptions and RMP)

        :param term: **string/int** ID of the term
        :return: **dict** All data for the term
        """
        responsedict = {}

        classes = self.db.UAlbertaCourseList.find({'term': int(term)})
        for course in classes:
            del course["_id"]

            if course["subject"] not in responsedict:
                responsedict[course["subject"]] = {}

            if course["coursenum"] not in responsedict[course["subject"]]:
                responsedict[course["subject"]][course["coursenum"]] = {"classes": []}

            subj = course["subject"]
            coursen = course["coursenum"]

            if "description" not in responsedict[subj][coursen]:
                result = self.db.UAlbertaCourseDesc.find_one({"coursenum": coursen, "subject": subj})

                if result:
                    # Remove unneeded fields
                    del result["_id"]
                    del result["subject"]
                    del result["coursenum"]
                    del result["lastModified"]

                    responsedict[subj][coursen]["description"] = result
                else:
                    responsedict[subj][coursen]["description"] = False

            # Remove unneeded fields
            del course["subject"]
            del course["coursenum"]
            del course["lastModified"]

            # Add this class to the course list
            responsedict[subj][coursen]["classes"].append(course)

        # Add the faculty sorting and course descriptions
        responsedict = self.retrieveCourseDesc(responsedict)

        # Send over a list of all the professors with a RMP rating in the list
        return {"classes": responsedict, "rmp": {}}

    def parseCourseDescription(self, req):
        char = 1
        while not req[char].isalpha():
                        char += 1
        return req[char:]

    def scrapeCourseDesc(self, conn, termid):
        log.info('obtaining course description')
        searchBase = 'term=' + termid + ', ou=calendar, dc=ualberta, dc=ca'
        entry_list = conn.extend.standard.paged_search(search_base=searchBase,
                                                       search_filter='(course=*)',
                                                       search_scope=LEVEL,
                                                       attributes=['catalog', 'courseDescription', 'courseTitle',
                                                                   'subject', 'units'],
                                                       paged_size=400,
                                                       generator=False)
        for entry in entry_list:
            courseDesc = {
                'coursenum': entry['attributes']['catalog'],
                'subject': entry['attributes']['subject'],
                'name': entry['attributes']['courseTitle'],
                'units': entry['attributes']['units']
            }
            if 'courseDescription' in entry['attributes']:
                if 'Prerequisite' in entry['attributes']['courseDescription']:
                    prereq = str(entry['attributes']['courseDescription']).split("Prerequisite", 1)[1]
                    prereq = self.parseCourseDescription(prereq)
                    courseDesc['prerequisites'] = prereq
                if "Antirequisite" in entry['attributes']['courseDescription']:
                    antireq = str(entry['attributes']['courseDescription']).split("Antirequisite", 1)[1]
                    antireq = self.parseCourseDescription(antireq)
                    courseDesc['antirequisite'] = antireq
                if "Corerequisite" in entry['attributes']['courseDescription']:
                    corereq = str(entry['attributes']['courseDescription']).split("Corerequisite", 1)[1]
                    corereq = self.parseCourseDescription(corereq)
                    courseDesc['corerequisites'] = corereq
                if "Note:" in entry['attributes']['courseDescription']:
                    note = str(entry['attributes']['courseDescription']).split("Note:", 1)[1]
                    courseDesc['notes'] = note
                courseDesc['desc'] = entry['attributes']['courseDescription']
            self.db.UAlbertaCourseDesc.update(
                {'coursenum': entry['attributes']['catalog']}, {'$set': courseDesc,
                                                                '$currentDate': {'lastModified': True}
                                                                },
                upsert=True
            )

    def UidToName(self, uid):
        r = requests.session().post("http://webapps.srv.ualberta.ca/search/?type=simple&uid=true&c=" + str(uid), verify=False)
        if r.status_code == requests.codes.ok:
            soup = BeautifulSoup(r.text, 'lxml')
            teacher = ''
            soup._find_one("b")
        print(r)

    def scrapeCourseList(self, conn, termid):
        searchBase = 'term=' + termid + ', ou=calendar, dc=ualberta, dc=ca'
        entry_list = conn.extend.standard.paged_search(search_base=searchBase,
                                                       search_filter='(&(!(textbook=*))(class=*)(!(classtime=*)))',
                                                       search_scope=SUBTREE,
                                                       attributes=['asString', 'class', 'term', 'campus',
                                                                   'classNotes', 'component', 'enrollStatus',
                                                                   'course', 'instructorUid'],
                                                       paged_size=400,
                                                       generator=False)
        log.info('parsing course data')
        for entry in entry_list:
            self.UidToName(entry['attributes']['instructorUid'])
            info = str(entry['attributes']['asString']).split(" ")
            if not info[1].isdigit():
                subject = info[0] + " " + info[1]
                coursenum = info[2]
            else:
                subject = info[0]
                coursenum = info[1]
            term = entry['attributes']['term'][0]
            if 'classNotes' in entry['attributes']:
                self.db.UAlbertaCourseList.update(
                    {'id': str(entry['attributes']['class'])},
                    {'$set': {"subject": subject, "term": term,
                              "coursenum": coursenum, "id": str(entry['attributes']['class']),
                              "location": str(entry['attributes']['campus']),
                              "notes": entry['attributes']['classNotes'],
                              "type": entry['attributes']['component'],
                              "status": entry['attributes']['enrollStatus'],
                              "group": entry['attributes']['course']},
                     '$currentDate': {'lastModified': True}
                     },
                    upsert=True
                )
            else:
                self.db.UAlbertaCourseList.update(
                    {'id': str(entry['attributes']['class'])},
                    {'$set': {"subject": subject, "term": term,
                              "coursenum": coursenum, "id": str(entry['attributes']['class']),
                              "location": str(entry['attributes']['campus']),
                              "type": entry['attributes']['component'],
                              "status": entry['attributes']['enrollStatus'],
                              "group": entry['attributes']['course']},
                     '$currentDate': {'lastModified': True}
                     },
                    upsert=True
                )
        entry_list = conn.extend.standard.paged_search(search_base=searchBase,
                                                       search_filter='(&(!(textbook=*))(class=*)(classtime=*))',
                                                       search_scope=SUBTREE,
                                                       attributes=['day', 'class', 'startTime', 'endTime',
                                                                   'location'],
                                                       paged_size=400,
                                                       generator=False)
        log.info('Matching additional data to course list')
        for entry in entry_list:
            duration = " "
            duration = duration.join((entry['attributes']['day'][0], entry['attributes']['startTime'][0],
                                      entry['attributes']['endTime'][0]))
            if 'location' in entry['attributes']:
                self.db.UAlbertaCourseList.update(
                    {'id': str(entry['attributes']['class'])},
                    {'$set': {'rooms': entry['attributes']['location'],
                              'times': duration},
                     '$currentDate': {'lastModified': True}
                     },
                    upsert=True
                )
            else:
                self.db.UAlbertaCourseList.update(
                    {'id': str(entry['attributes']['class'])},
                    {'$set': {'times': duration},
                     '$currentDate': {'lastModified': True}
                     },
                    upsert=True
                )
        totalEntries = len(entry_list)
        log.info('Upserted ' + str(totalEntries) + ' course list entries')

    def scrapeTerms(self, conn):
        conn.search(search_base='ou=calendar, dc=ualberta, dc=ca', search_filter='(term=*)', search_scope=LEVEL,
                    attributes=['term', 'termTitle'])
        for entry in conn.entries:
            if int(str(entry['term'])) >= 1566:
                self.db.UAlbertaTerms.update(
                    {str(entry['term']): str(entry['termTitle'])},
                    {'$set': {str(entry['term']): str(entry['termTitle'])}},
                    upsert=True
                )
        return conn.entries

    def updateFaculties(self, conn):
        log.info("Getting faculty list")
        searchBase = 'term='+str(self.scrapeTerms(conn)[len(conn.entries) - 1]['term'])+', ou=calendar, dc=ualberta, dc=ca'
        log.info("Updating faculties with search base " + searchBase)
        entry_list = conn.extend.standard.paged_search(search_base=searchBase,
                                                       search_filter='(term=*)',
                                                       search_scope=LEVEL,
                                                       attributes=['subject', 'subjectTitle', 'faculty'],
                                                       paged_size=400,
                                                       generator=False)
        for entry in entry_list:
            if 'subject' in entry['attributes']:
                self.db.UAlbertaSubjects.update(
                    {'subject': entry['attributes']['subject']},
                    {'$set': {'subject': entry['attributes']['subject'], 'faculty': entry['attributes']['faculty'],
                              'name': entry['attributes']['subjectTitle']},
                     '$currentDate': {'lastModified': True}
                    },
                    upsert=True
                )
        log.info('Finished updating faculties')

    def run(self):
        """
        Scraping thread that obtains updated course info

        :return:
        """

        if self.settings["scrape"]:
            while True:
                try:
                    server = Server('directory.srv.ualberta.ca', get_info=ALL)
                    conn = Connection(server, auto_bind=True)
                    #self.updateFaculties(conn)
                    self.terms = self.getTerms()
                    for term in self.terms:
                        if int(term) >= 1566:
                            log.info('Obtaining ' + self.terms[term] + ' course data with id ' + term)
                            self.scrapeCourseList(conn, term)
                            #self.scrapeCourseDesc(conn, term)
                    log.info('Finished scraping for UAlberta data')
                    pass
                except Exception as e:
                    log.critical("There was an critical exception | " + str(e))

                # Sleep for the specified interval
                time.sleep(self.settings["scrapeinterval"])
        else:
            log.info("Scraping is disabled")
