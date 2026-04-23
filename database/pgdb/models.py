from sqlalchemy import Column, Integer, Text, Float
from sqlalchemy.orm import declarative_base
from pgvector.sqlalchemy import Vector

Base = declarative_base()


class CPTEmbedding(Base):
    __tablename__ = "cpt_embeddings"

    id = Column(Integer, primary_key=True)
    pro_code = Column("procode", Text, unique=True, nullable=False)
    code_desc = Column("codedesc", Text)
    pro_name = Column("proname", Text)
    associated_with_pro_code = Column("associatedwithprocode", Text)
    min_qty = Column("minqty", Integer)
    max_qty = Column("maxqty", Integer)
    min_size = Column("minsize", Text)
    max_size = Column("maxsize", Text)
    charge_per_unit = Column("chargeperunit", Float)
    embedding = Column(Vector(1536))


class EMEmbedding(Base):
    __tablename__ = "em_embeddings"

    id = Column(Integer, primary_key=True)
    enm_code = Column("enmcode", Text, unique=True, nullable=False)
    enm_code_desc = Column("enmcodedesc", Text)
    encounter_time = Column("encountertime", Text)
    enm_level = Column("enmlevel", Integer)
    embedding = Column(Vector(1536))


class ModifierEmbedding(Base):
    __tablename__ = "modifier_embeddings"

    id = Column(Integer, primary_key=True)
    modifier = Column(Text, unique=True, nullable=False)
    modifier_desc = Column("modifierdesc", Text)
    modifier_det_desc = Column("modifierdetdesc", Text)
    embedding = Column(Vector(1536))