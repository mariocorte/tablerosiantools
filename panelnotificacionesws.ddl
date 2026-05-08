-- public.codigosnot definition

-- Drop table

-- DROP TABLE public.codigosnot;

CREATE TABLE public.codigosnot (
	codigosnotid int2 DEFAULT nextval('codigosnotid'::regclass) NOT NULL,
	codigosnotcod varchar(40) NOT NULL,
	CONSTRAINT codigosnot_pkey PRIMARY KEY (codigosnotid)
);


-- public.dbguser definition

-- Drop table

-- DROP TABLE public.dbguser;

CREATE TABLE public.dbguser (
	dbguserid int8 DEFAULT nextval('dbguserid'::regclass) NOT NULL,
	dbgusername varchar(10) NULL,
	dbguserfecha date NULL,
	dbguservalor text NULL,
	dbguserprograma varchar(80) NULL,
	CONSTRAINT dbguser_pkey PRIMARY KEY (dbguserid)
);


-- public.debuglog definition

-- Drop table

-- DROP TABLE public.debuglog;

CREATE TABLE public.debuglog (
	debuglogid int4 DEFAULT nextval('debuglogid'::regclass) NOT NULL,
	debuglogdetalle text NOT NULL,
	debuglogdate date NULL,
	CONSTRAINT debuglog_pkey PRIMARY KEY (debuglogid)
);


-- public.ejecproc definition

-- Drop table

-- DROP TABLE public.ejecproc;

CREATE TABLE public.ejecproc (
	ejecprocid int8 DEFAULT nextval('ejecprocid'::regclass) NOT NULL,
	procesosatid int8 NOT NULL,
	ejecprocfecha timestamp NOT NULL,
	ejecprocresultado int2 NOT NULL,
	ejecprocobservaciones varchar(400) NULL,
	CONSTRAINT ejecproc_pkey PRIMARY KEY (ejecprocid)
);
CREATE INDEX iejecproc1 ON public.ejecproc USING btree (procesosatid);


-- public.notificacion_escrito definition

-- Drop table

-- DROP TABLE public.notificacion_escrito;

CREATE TABLE public.notificacion_escrito (
	ced_cod varchar(10) NOT NULL,
	esc_cod varchar(10) NOT NULL,
	esc_descr varchar(150) NOT NULL,
	esc_activo int2 NOT NULL,
	CONSTRAINT notificacion_escrito_pkey PRIMARY KEY (ced_cod)
);


-- public.procesosat definition

-- Drop table

-- DROP TABLE public.procesosat;

CREATE TABLE public.procesosat (
	procesosatid int8 DEFAULT nextval('procesosatid'::regclass) NOT NULL,
	procesosatnombre varchar(40) NOT NULL,
	procesosatdescripcion varchar(400) NULL,
	procesosatultiej timestamp NULL,
	procesosatprxej timestamp NULL,
	CONSTRAINT procesosat_pkey PRIMARY KEY (procesosatid)
);


-- public.programacionpr definition

-- Drop table

-- DROP TABLE public.programacionpr;

CREATE TABLE public.programacionpr (
	programacionprid int8 DEFAULT nextval('programacionprid'::regclass) NOT NULL,
	procesosatid int8 NOT NULL,
	programacionprtipo int2 NULL,
	programacionprim int2 NULL,
	programacionprds int2 NULL,
	programacionprdm int2 NULL,
	programacionprfechafija timestamp NULL,
	programacionprinicio timestamp NULL,
	programacionprfin timestamp NULL,
	CONSTRAINT programacionpr_pkey PRIMARY KEY (programacionprid)
);
CREATE INDEX iprogramacionpr1 ON public.programacionpr USING btree (procesosatid);


-- public.reportessian definition

-- Drop table

-- DROP TABLE public.reportessian;

CREATE TABLE public.reportessian (
	reportessianid int8 DEFAULT nextval('reportessianid'::regclass) NOT NULL,
	reportessiannombre varchar(40) NULL,
	reportessianurl varchar(400) NULL,
	reportessianprocedure varchar(40) NULL,
	reportessianobs varchar(400) NULL,
	CONSTRAINT reportessian_pkey PRIMARY KEY (reportessianid)
);


-- public.retornomp definition

-- Drop table

-- DROP TABLE public.retornomp;

CREATE TABLE public.retornomp (
	pmovimientoid int8 NOT NULL,
	pactuacionid int8 NOT NULL,
	pdomicilioelectronicopj varchar(255) NOT NULL,
	contenido_xml xml NOT NULL,
	fechacreacion timestamptz DEFAULT now() NOT NULL,
	ultactualizacion timestamptz DEFAULT now() NOT NULL,
	procesado bool DEFAULT false NOT NULL,
	fechaproceso timestamptz NULL,
	CONSTRAINT retornomp_pk PRIMARY KEY (pmovimientoid, pactuacionid, pdomicilioelectronicopj)
);

-- Table Triggers

create trigger trg_retornomp_set_ultactualizacion before
insert
    or
update
    on
    public.retornomp for each row execute procedure trg_retornomp_set_ultactualizacion();


-- public.secobject definition

-- Drop table

-- DROP TABLE public.secobject;

CREATE TABLE public.secobject (
	secobjectname varchar(120) NOT NULL,
	CONSTRAINT secobject_pkey PRIMARY KEY (secobjectname)
);


-- public.secrole definition

-- Drop table

-- DROP TABLE public.secrole;

CREATE TABLE public.secrole (
	secroleid int2 DEFAULT nextval('secroleid'::regclass) NOT NULL,
	secrolename varchar(40) NOT NULL,
	secroledescription varchar(120) NOT NULL,
	CONSTRAINT secrole_pkey PRIMARY KEY (secroleid)
);


-- public.secuser definition

-- Drop table

-- DROP TABLE public.secuser;

CREATE TABLE public.secuser (
	secuserid int2 DEFAULT nextval('secuserid'::regclass) NOT NULL,
	secusername varchar(100) NOT NULL,
	secuserpassword varchar(100) NOT NULL,
	CONSTRAINT secuser_pkey PRIMARY KEY (secuserid)
);


-- public.tablerospar definition

-- Drop table

-- DROP TABLE public.tablerospar;

CREATE TABLE public.tablerospar (
	par_id bigserial NOT NULL,
	par_grupo varchar(50) NOT NULL,
	par_clave varchar(100) NOT NULL,
	par_subclave varchar(100) NULL,
	par_valor text NULL,
	par_valor_json jsonb NULL,
	par_tipo varchar(20) NOT NULL,
	par_ambiente varchar(20) DEFAULT 'TODOS'::character varying NOT NULL,
	par_activo bool DEFAULT true NOT NULL,
	par_descripcion varchar(500) NULL,
	par_fecha_alta timestamp DEFAULT now() NOT NULL,
	par_fecha_modif timestamp NULL,
	par_usuario_alta varchar(50) NULL,
	par_usuario_modif varchar(50) NULL,
	CONSTRAINT ck_tablerospar_valor CHECK (((((par_tipo)::text = 'JSON'::text) AND (par_valor_json IS NOT NULL)) OR ((par_tipo)::text = 'FLAG'::text) OR (((par_tipo)::text = ANY ((ARRAY['STRING'::character varying, 'NUMBER'::character varying, 'BOOLEAN'::character varying])::text[])) AND (par_valor IS NOT NULL)))),
	CONSTRAINT tablerospar_par_ambiente_check CHECK (((par_ambiente)::text = ANY ((ARRAY['PROD'::character varying, 'HOMO'::character varying, 'DEV'::character varying, 'TODOS'::character varying])::text[]))),
	CONSTRAINT tablerospar_par_tipo_check CHECK (((par_tipo)::text = ANY ((ARRAY['STRING'::character varying, 'NUMBER'::character varying, 'BOOLEAN'::character varying, 'JSON'::character varying, 'FLAG'::character varying])::text[]))),
	CONSTRAINT tablerospar_pkey PRIMARY KEY (par_id),
	CONSTRAINT uk_tablerospar UNIQUE (par_grupo, par_clave, par_subclave, par_ambiente)
);
CREATE INDEX ix_tablerospar_busq ON public.tablerospar USING btree (par_grupo, par_clave, par_activo);
CREATE INDEX ix_tablerospar_json ON public.tablerospar USING gin (par_valor_json);


-- public.usercustomizations definition

-- Drop table

-- DROP TABLE public.usercustomizations;

CREATE TABLE public.usercustomizations (
	usercustomizationsid int4 NOT NULL,
	usercustomizationskey varchar(200) NOT NULL,
	usercustomizationsvalue text NOT NULL,
	CONSTRAINT usercustomizations_pkey PRIMARY KEY (usercustomizationsid, usercustomizationskey)
);


-- public.visornotificaciones definition

-- Drop table

-- DROP TABLE public.visornotificaciones;

CREATE TABLE public.visornotificaciones (
	visornotificacionesid int4 DEFAULT nextval('visornotificacionesid'::regclass) NOT NULL,
	visornotificacionesdatos bytea NOT NULL,
	visornotificacionesdatos_gxi varchar(2048) NOT NULL,
	pmovimientoid int8 NOT NULL,
	pactuacionid int8 NOT NULL,
	pdomicilioelectronicopj varchar(10) NOT NULL,
	visornotificacionespaso1 bytea NOT NULL,
	CONSTRAINT visornotificaciones_pkey PRIMARY KEY (visornotificacionesid)
);
CREATE INDEX ivisornotificaciones1 ON public.visornotificaciones USING btree (pmovimientoid, pactuacionid, pdomicilioelectronicopj);


-- public.wwp_parameter definition

-- Drop table

-- DROP TABLE public.wwp_parameter;

CREATE TABLE public.wwp_parameter (
	wwpparameterkey varchar(300) NOT NULL,
	wwpparametercategory varchar(200) NOT NULL,
	wwpparameterdescription varchar(200) NOT NULL,
	wwpparametervalue text NOT NULL,
	wwpparameterdisabledelete bool NOT NULL,
	CONSTRAINT wwp_parameter_pkey PRIMARY KEY (wwpparameterkey)
);


-- public.juzgadosxrol definition

-- Drop table

-- DROP TABLE public.juzgadosxrol;

CREATE TABLE public.juzgadosxrol (
	juzgadosxrolid int2 DEFAULT nextval('juzgadosxrolid'::regclass) NOT NULL,
	secroleid int2 NOT NULL,
	pdomicilioelectronicopj varchar(10) NOT NULL,
	distrito_id int4 NULL,
	CONSTRAINT juzgadosxrol_pkey PRIMARY KEY (juzgadosxrolid),
	CONSTRAINT ijuzgadosxrol1 FOREIGN KEY (secroleid) REFERENCES public.secrole(secroleid)
);
CREATE INDEX ijuzgadosxrol1 ON public.juzgadosxrol USING btree (secroleid);


-- public.secfunctionality definition

-- Drop table

-- DROP TABLE public.secfunctionality;

CREATE TABLE public.secfunctionality (
	secfunctionalityid int8 DEFAULT nextval('secfunctionalityid'::regclass) NOT NULL,
	secfunctionalitykey varchar(100) NOT NULL,
	secfunctionalitydescription varchar(100) NOT NULL,
	secfunctionalitytype int2 NOT NULL,
	secparentfunctionalityid int8 NULL,
	secfunctionalityactive bool NOT NULL,
	CONSTRAINT secfunctionality_pkey PRIMARY KEY (secfunctionalityid),
	CONSTRAINT isecfunctionality1 FOREIGN KEY (secparentfunctionalityid) REFERENCES public.secfunctionality(secfunctionalityid)
);
CREATE INDEX isecfunctionality1 ON public.secfunctionality USING btree (secparentfunctionalityid);
CREATE UNIQUE INDEX usecfunctionality ON public.secfunctionality USING btree (secfunctionalitykey);


-- public.secfunctionalityrole definition

-- Drop table

-- DROP TABLE public.secfunctionalityrole;

CREATE TABLE public.secfunctionalityrole (
	secfunctionalityid int8 NOT NULL,
	secroleid int2 NOT NULL,
	CONSTRAINT secfunctionalityrole_pkey PRIMARY KEY (secfunctionalityid, secroleid),
	CONSTRAINT isecfunctionalityrol1 FOREIGN KEY (secfunctionalityid) REFERENCES public.secfunctionality(secfunctionalityid),
	CONSTRAINT isecfunctionalityrol2 FOREIGN KEY (secroleid) REFERENCES public.secrole(secroleid)
);
CREATE INDEX isecfunctionalityrol2 ON public.secfunctionalityrole USING btree (secroleid);


-- public.secobjectfunctionalities definition

-- Drop table

-- DROP TABLE public.secobjectfunctionalities;

CREATE TABLE public.secobjectfunctionalities (
	secobjectname varchar(120) NOT NULL,
	secfunctionalityid int8 NOT NULL,
	CONSTRAINT secobjectfunctionalities_pkey PRIMARY KEY (secobjectname, secfunctionalityid),
	CONSTRAINT isecobjectfunctionalities1 FOREIGN KEY (secfunctionalityid) REFERENCES public.secfunctionality(secfunctionalityid),
	CONSTRAINT isecobjectfunctionalities2 FOREIGN KEY (secobjectname) REFERENCES public.secobject(secobjectname)
);
CREATE INDEX isecobjectfunctionalities1 ON public.secobjectfunctionalities USING btree (secfunctionalityid);


-- public.secuserrole definition

-- Drop table

-- DROP TABLE public.secuserrole;

CREATE TABLE public.secuserrole (
	secuserid int2 NOT NULL,
	secroleid int2 NOT NULL,
	CONSTRAINT secuserrole_pkey PRIMARY KEY (secuserid, secroleid),
	CONSTRAINT isecuserrole1 FOREIGN KEY (secroleid) REFERENCES public.secrole(secroleid),
	CONSTRAINT isecuserrole2 FOREIGN KEY (secuserid) REFERENCES public.secuser(secuserid)
);
CREATE INDEX isecuserrole1 ON public.secuserrole USING btree (secroleid);


-- public.menuitem definition

-- Drop table

-- DROP TABLE public.menuitem;

CREATE TABLE public.menuitem (
	menuitemid int2 DEFAULT nextval('menuitemid'::regclass) NOT NULL,
	menuitemcaption varchar(40) NOT NULL,
	menuitemorder int2 NOT NULL,
	menuitemfatherid int2 NULL,
	menuitemtype int2 NOT NULL,
	menuitemlink varchar(400) NOT NULL,
	menuitemlinkparameters varchar(100) NOT NULL,
	menuitemlinktarget varchar(10) NOT NULL,
	menuitemiconclass varchar(40) NOT NULL,
	menuitemshowdevelopermenuoptio bool NOT NULL,
	menuitemshoweditmenuoptions bool NOT NULL,
	secfunctionalityid int8 NULL,
	CONSTRAINT menuitem_pkey PRIMARY KEY (menuitemid),
	CONSTRAINT imenuitem1 FOREIGN KEY (menuitemfatherid) REFERENCES public.menuitem(menuitemid),
	CONSTRAINT imenuitem2 FOREIGN KEY (secfunctionalityid) REFERENCES public.secfunctionality(secfunctionalityid)
);
CREATE INDEX imenuitem1 ON public.menuitem USING btree (menuitemfatherid);
CREATE INDEX imenuitem2 ON public.menuitem USING btree (secfunctionalityid);