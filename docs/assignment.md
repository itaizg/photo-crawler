# Assignment

## Exercise Overview

In this exercise, you will implement a system that crawls a website, extracts images containing people with visible faces, and indexes them for image-based search. After the crawl is complete, the system should allow the user to provide an image of a specific character and return a list of all pages on the site that contain an image of that same character.

## Pipeline Requirements

Your task is to build a pipeline that:

1. Scrapes images from the given website containing people with visible faces.
2. Converts all images to a single format (e.g., JPEG or PNG) and resizes them to a fixed maximum size.
3. Detects faces and extracts embeddings for all detected faces using a pre-trained deep learning model.
4. Stores results (image metadata, face bounding boxes, and face embeddings) in a database.

## Search Script Requirements

Implement the search in a separate script that:

1. Takes a new image as input.
2. Extracts face embeddings from the input image.
3. Searches for similar faces using vector similarity.
4. Returns the pages containing images of the same character with high confidence.

## Technical Requirements

* Use Python for implementation.
* Orchestrate the pipeline with Apache Airflow.
* Run everything using Docker Compose (Airflow + database + any other required services).
* We encourage the use of AI-assisted tools for code development. However, you are fully responsible for the quality, correctness, and outcomes of the code. For evaluation purposes, there is no distinction between code authored directly by you and code generated with the assistance of AI.

## Recommended Tools & Resources

* For face recognition: Consider using [DeepFace ](https://t.eu.lever-analytics.com/email-link?dest=https%3A%2F%2Fgithub.com%2Fserengil%2Fdeepface&eid=669aaa38-58d2-4ddf-bbd7-d1a40ac1e2aa&idx=0&token=7MDij4ZU2qQWYeMiC5cUT64vsk8)- a lightweight face recognition and facial attribute analysis framework that supports multiple state-of-the-art models (VGG-Face, FaceNet, OpenFace, DeepFace, DeepID, ArcFace, Dlib, SFace).
* Helpful tutorial: [A Gentle Introduction to Face Recognition in Deep Learning ](https://t.eu.lever-analytics.com/email-link?dest=https%3A%2F%2Fsefiks.com%2F2020%2F05%2F01%2Fa-gentle-introduction-to-face-recognition-in-deep-learning%2F&eid=669aaa38-58d2-4ddf-bbd7-d1a40ac1e2aa&idx=1&token=NqThLVa9pI7s_5jLsvtfQwQsdPk)- provides excellent background on face recognition concepts and implementation approaches.

## Deliverables

* A GitHub repository containing:
   * Source code for the Airflow pipeline.
   * Face matching script (face_matcher.py or similar).
   * docker-compose.yml to run the entire project.
   * A short README including:
      * How to run the pipeline.
      * How to use the face matching script.
      * Libraries/models used.
      * Your approach and architecture decisions.
      * Example usage and expected outputs.
* Before starting, please estimate the time required and send your estimate so we can schedule a follow-up interview.
