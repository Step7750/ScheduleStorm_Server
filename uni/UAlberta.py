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
import re
from ldap3 import Server, Connection, SUBTREE, ALL, LEVEL

log = logging.getLogger("UAlberta")


class UAlberta(threading.Thread):
    def __init__(self, settings):
        threading.Thread.__init__(self)
        self.settings = settings
        self.db = pymongo.MongoClient().ScheduleStorm

        log.info("Ensuring MongoDB indexes exist")

        # want to add indexes (if they already exist, nothing will happen)
        self.db.UAlbertaCourseDesc.create_index([
            ("coursenum", pymongo.ASCENDING),
            ("subject", pymongo.ASCENDING)],
            unique=True)

        self.db.UAlbertaCourseList.create_index([
            ("id", pymongo.ASCENDING),
            ("term", pymongo.ASCENDING)],
            unique=True)

        self.db.UAlbertaSubjects.create_index([
            ("subject", pymongo.ASCENDING)],
            unique=True)

        self.db.UAlbertaProfessor.create_index([
            ("uid", pymongo.ASCENDING)],
            unique=True)

        self.db.UAlbertaTerms.create_index([
            ("term", pymongo.ASCENDING)],
            unique=True)

    def getTerms(self):
        """
        API Handler

        Returns the distinct terms in the database, along with their name and id

        :return: **dict** Keys are the ids, values are the proper names
        """
        termlist = self.db.UAlbertaCourseList.distinct("term")
        responsedict = {}
        for term in termlist:
            responsedict[str(term)] = self.db.UAlbertaTerms.find_one({"term": str(term)})['termTitle']
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

        distinctProfessors = []

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
            for professor in course['teachers']:
                if professor not in distinctProfessors:
                    distinctProfessors.append(professor)

        # Add the faculty sorting and course descriptions
        responsedict = self.retrieveCourseDesc(responsedict)

        # Match RMP data
        rmpobj = self.matchRMPNames(distinctProfessors)
        # Send over a list of all the professors with a RMP rating in the list
        return {"classes": responsedict, "rmp": rmpobj}

    def matchRMPNames(self, distinctteachers):
        """
        Given a list of teachers to match RMP data to, this function obtains all RMP data and tries to match the names
        with the distinctteachers list and returns the matches

        We first check whether the constructed name is simply the same in RMP
        If not, we check whether the first and last words in a name in RMP is the same
        If not, we check whether any first and last words in the teachers name has a result in RMP that starts
            with the first and last words
        If not, we give up and don't process the words

        Most teachers should have a valid match using this method, many simply don't have a profile on RMP
        Around 80%+ of valid teachers on RMP should get a match

        False positives are possible, but highly unlikely given that it requires the first and last name of the
        wrong person to start the same way

        :param distinctteachers: **list** Distinct list of all teachers to find an RMP match for
        :return: **dict** Matched teachers and their RMP ratings
        """
        # Get the RMP data for all teachers at UAlberta
        rmp = self.db.RateMyProfessors.find({"school": self.settings["rmpid"]})

        returnobj = {}
        # We want to construct the names of each teacher and invert the results for easier parsing
        # and better time complexity
        rmpinverted = {}
        for teacher in rmp:
            # Construct the name
            fullname = ""
            if "firstname" in teacher:
                fullname += teacher["firstname"]
            if "middlename" in teacher:
                fullname += " " + teacher["middlename"]
            if "lastname" in teacher:
                fullname += " " + teacher["lastname"]

            # remove unnecessary fields
            del teacher["_id"]
            del teacher["lastModified"]
            del teacher["school"]

            rmpinverted[fullname] = teacher

        # Iterate through each distinct teacher
        for teacher in distinctteachers:
            if teacher in rmpinverted:
                # We found an instant match, add it to the return dict
                returnobj[teacher] = rmpinverted[teacher]
            else:
                # Find the first and last words of the name
                teacherNameSplit = teacher.split(" ")
                lastword = teacherNameSplit[-1]
                firstword = teacherNameSplit[0]

                # Check to see if the first and last words find a match (without a middle name)
                namewithoutmiddle = firstword + " " + lastword

                if namewithoutmiddle in rmpinverted:
                    # Found the match! Add an alias field
                    returnobj[teacher] = rmpinverted[namewithoutmiddle]
                else:
                    # Find a teacher in RMP that had the first and last words of their name starting the
                    # respective words in the original teacher's name
                    for teacher2 in rmpinverted:
                        splitname = teacher2.split(" ")
                        first = splitname[0]
                        last = splitname[-1]

                        if lastword.startswith(last) and firstword.startswith(first):
                            returnobj[teacher] = rmpinverted[teacher2]
                            break

        return returnobj

    def parseCourseDescription(self, req):
        char = 1
        while not req[char].isalpha():
                        char += 1
        return req[char:]

    def scrapeCourseDesc(self, conn, termid):
        log.info('obtaining course descriptions')
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
                desc = entry['attributes']['courseDescription']
                if "See Note" in desc:
                    desc = desc.split("See Note", 1)[0]
                if 'Prerequisite' in desc:
                    info = desc.split("Prerequisite", 1)
                    prereq = self.parseCourseDescription(info[1])
                    desc = info[0]
                    if "Corequisite" in prereq or "corequisite" in prereq:
                        if "Corequisite" in prereq:
                            info = prereq.split("Corequisite", 1)
                        elif "corequisite" in prereq:
                            info = prereq.split("corequisite", 1)

                        prereq = info[0]
                        if prereq[-4:] == "and ":
                            prereq = prereq[:-4]
                        if len(info[1]) != 1:
                            corereq = self.parseCourseDescription(info[1])
                            if prereq == "or ":
                                prereq = corereq
                            else:
                                if corereq != prereq:
                                    courseDesc['coreq'] = corereq

                    if "Note:" in prereq:
                        note = prereq.split("Note:", 1)
                        courseDesc['notes'] = note[1]
                        prereq = note[0]

                    courseDesc['prereq'] = prereq
                if "Antirequisite" in desc:
                    antireq = desc.split("Antirequisite", 1)[1]
                    antireq = self.parseCourseDescription(antireq)
                    courseDesc['antireq'] = antireq
                    desc = antireq[0]
                if desc[-4:] == "and ":
                            desc = desc[:-4]
                courseDesc['desc'] = desc

            self.db.UAlbertaCourseDesc.update(
                {'coursenum': entry['attributes']['catalog'], 'subject': entry['attributes']['subject']},
                {
                    '$set': courseDesc,
                    '$currentDate': {'lastModified': True}
                },
                upsert=True
            )

    def UidToName(self, uid):
        professor = self.db.UAlbertaProfessor.find({"uid": uid})
        if professor.count() == 0:
            r = requests.get("http://webapps.srv.ualberta.ca/search/?type=simple&uid=true&c=" + uid, verify=False)
            if r.status_code == requests.codes.ok:
                soup = BeautifulSoup(r.text, "lxml")
                for tag in soup.find_all("b"):
                    info = tag.text
                    if info != "Dr " and info != "Prof ":
                        professor = info
                        break
                log.info('adding uid ' + uid + ' to UAlbertaProfessor db with professor name ' + professor)
                self.db.UAlbertaProfessor.update({"uid": uid}, {'$set': {"uid": uid, "Name": professor}},
                                                 upsert=True)
        else:
            professor = professor[0]['Name']
        return professor

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
            info = str(entry['attributes']['asString']).split(" ")
            if not info[1].isdigit():
                subject = info[0] + " " + info[1]
                coursenum = info[2]
            else:
                subject = info[0]
                coursenum = info[1]
            if entry['attributes']['enrollStatus'] == "O":
                status = "Open"
            elif entry['attributes']['enrollStatus'] == "C":
                status = "Closed"
            else:
                status = entry['attributes']['enrollStatus']
            courseList = {"subject": subject, "term": entry['attributes']['term'][0], "coursenum": coursenum,
                          "id": str(entry['attributes']['class']), "location": str(entry['attributes']['campus']),
                          "type": entry['attributes']['component'], "status": status,
                          "group": entry['attributes']['course'], "times": ["N/A"], "rooms": ["N/A"]}
            if 'instructorUid' in entry['attributes']:
                courseList['teachers'] = [self.UidToName(entry['attributes']['instructorUid'][0])]
            else:
                courseList['teachers'] = ["N/A"]
            if 'classNotes' in entry['attributes']:
                courseList["notes"] = entry['attributes']['classNotes'][0]
            self.db.UAlbertaCourseList.update(
                {'id': str(entry['attributes']['class'])},
                {'$set': courseList, '$currentDate': {'lastModified': True}},
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
            duration = duration.join((entry['attributes']['day'][0], entry['attributes']['startTime'][0].replace(" ", ""),
                                      entry['attributes']['endTime'][0].replace(" ", "")))

            duration = re.sub(r'^((.*?\s.*?){1})\s', r'\1 - ', duration)
            courseList = {'times': [duration]}

            if 'location' in entry['attributes']:
                courseList['rooms'] = [entry['attributes']['location']]
            self.db.UAlbertaCourseList.update(
                {'id': str(entry['attributes']['class'])},
                {'$set': courseList, '$currentDate': {'lastModified': True}},
                upsert=True
            )

    def scrapeTerms(self, conn):
        conn.search(search_base='ou=calendar, dc=ualberta, dc=ca', search_filter='(term=*)', search_scope=LEVEL,
                    attributes=['term', 'termTitle'])
        terms = []
        for entry in conn.entries:
            termDict = {}
            if int(str(entry['term'])) >= 1566:
                termDict['term'] = str(entry['term'])
                termDict['termTitle'] = str(entry['termTitle'])
                self.db.UAlbertaTerms.update(
                    {'term': str(entry['term'])},
                    {'$set': termDict},
                    upsert=True
                )
                terms.append(termDict)
        return terms

    def updateFaculties(self, conn):
        log.info("Getting faculty list")
        for term in self.scrapeTerms(conn):
            if int(term['term']) % 3 == 0 or int(term['term']) % 10 == 0:
                searchBase = 'term='+term['term']+', ou=calendar, dc=ualberta, dc=ca'
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
                    self.updateFaculties(conn)
                    terms = self.db.UAlbertaTerms.distinct("term")
                    for term in terms:
                        if int(term) >= 1566:
                            log.info('Obtaining ' + self.db.UAlbertaTerms.find({"term": term})[0]['termTitle'] + ' course data with id ' + term)
                            self.scrapeCourseList(conn, term)
                            self.scrapeCourseDesc(conn, term)
                    log.info('Finished scraping for UAlberta data')
                    pass
                except Exception as e:
                    log.critical("There was an critical exception | " + str(e))

                # Sleep for the specified interval
                time.sleep(self.settings["scrapeinterval"])
        else:
            log.info("Scraping is disabled")
